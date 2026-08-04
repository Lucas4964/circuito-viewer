# =============================================================================
# CAMADA DE FUNDO — TILES DE SATÉLITE (XYZ / Web Mercator, EPSG:3857)
#
# Fornece uma imagem de satélite georreferenciada como plano de fundo do canvas,
# no conceito do QuickMapServices (QGIS). Duas partes:
#   1) MATEMÁTICA DE TILES (pura, SEM Qt de janela — testável headless): converte
#      lon/lat ↔ índice de tile (z/x/y) e devolve a bbox geográfica de um tile.
#      É o que permite alinhar os tiles (Web Mercator) com a rede (UTM 21S) pela
#      MESMA pipeline geo↔cena do app (ver EditorBoilerplate._latlon_para_canvas).
#   2) GerenciadorTiles (QObject): baixa tiles de forma ASSÍNCRONA
#      (QNetworkAccessManager — já vem no PyQt6, sem dependência nova) com cache
#      de dois níveis (memória + disco). Degrada em silêncio: sem rede, o fundo
#      simplesmente não aparece (nunca derruba a UI).
#
# Provedor padrão: Esri World Imagery (tiles XYZ, sem chave de API; uso permitido
# COM ATRIBUIÇÃO na tela). O template de URL é plugável (dataclass Provedor), o
# que deixa Mapbox/Google-com-chave para depois sem mudar o resto.
# =============================================================================
import hashlib
import math
import os
from dataclasses import dataclass

# QtNetwork/QtGui só são necessários para o GerenciadorTiles; importar as classes
# não exige um QApplication vivo, então a parte de math continua testável headless.
from PyQt6.QtCore import QObject, pyqtSignal, QUrl, QStandardPaths, QDir
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply


# =============================================================================
# 1. MATEMÁTICA DE TILES (pura — sem estado, sem Qt de janela)
# =============================================================================

def lonlat_para_tile(lon, lat, z):
    """(lon, lat em graus, nível z) -> (xt, yt) índices INTEIROS do tile XYZ que
    contém o ponto (esquema Web Mercator / slippy map). Latitude é fixada em
    [-85.0511, 85.0511] (limite do Mercator) para evitar tan() explodir."""
    n = 2 ** z
    lat = max(-85.05112878, min(85.05112878, lat))
    lat_rad = math.radians(lat)
    xt = int((lon + 180.0) / 360.0 * n)
    yt = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    # Clampa aos limites válidos (bordas do mundo).
    xt = max(0, min(n - 1, xt))
    yt = max(0, min(n - 1, yt))
    return xt, yt


def tile_bbox(xt, yt, z):
    """Bbox geográfica de um tile: (lon_min, lat_min, lon_max, lat_max) em graus.
    lon_min/lat_max é o canto SUPERIOR-ESQUERDO do tile (y cresce para baixo, como
    na tela). Usada para posicionar o tile na cena pelos seus 4 cantos."""
    n = 2 ** z

    def _lat(y):
        return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))

    lon_min = xt / n * 360.0 - 180.0
    lon_max = (xt + 1) / n * 360.0 - 180.0
    lat_max = _lat(yt)         # borda superior
    lat_min = _lat(yt + 1)     # borda inferior
    return lon_min, lat_min, lon_max, lat_max


def nivel_zoom(px_por_metro, lat, z_min=0, z_max=19):
    """Escolhe o nível z cujo tile fica com ~256 px na tela para a resolução
    corrente. `px_por_metro` = pixels de tela por metro de solo (no app,
    escala_atual × m11 da view). Deriva de igualar a resolução do Web Mercator
    (156543.03·cos(lat)/2^z m/px) à resolução de tela (1/px_por_metro m/px):
        2^z = 156543.03 · cos(lat) · px_por_metro
    Resultado arredondado e limitado a [z_min, z_max]."""
    if px_por_metro <= 0:
        return z_min
    lat = max(-85.05112878, min(85.05112878, lat))
    alvo = 156543.03392 * math.cos(math.radians(lat)) * px_por_metro
    if alvo <= 1.0:
        return z_min
    z = round(math.log2(alvo))
    return max(z_min, min(z_max, z))


def cantos_lonlat_da_faixa(x_ini, y_ini, nx, ny, z):
    """Grade de (nx+1)×(ny+1) CANTOS da faixa de tiles, em ordem de linha
    (iy externo, ix interno). O canto (ix, iy) é o canto SUPERIOR-ESQUERDO do
    tile (x_ini+ix, y_ini+iy); a coluna extra ix=nx fecha a borda direita e a
    linha extra iy=ny fecha a borda inferior da faixa.

    É a grade de pontos de controle que elimina as frestas: como o canto direito
    de um tile É o esquerdo do vizinho (o mesmo elemento da grade), as bordas
    coincidem exatamente. Ver DiagramView._desenhar_mapa.

    ATENÇÃO ao componente do bbox: `tile_bbox` devolve
    (lon_min, lat_min, lon_max, lat_max) e o canto superior é **lat_max** — o
    índice é escrito explicitamente aqui porque desempacotar por posição já
    trocou lat_max por lat_min uma vez, e o efeito é sutil: o mosaico inteiro
    desce UMA linha de tiles (faixa em branco no topo, e a imagem "salta" a cada
    nível de zoom, já que a altura do tile dobra). Retorna (lons, lats)."""
    lons, lats = [], []
    for iy in range(ny + 1):
        for ix in range(nx + 1):
            bbox = tile_bbox(x_ini + ix, y_ini + iy, z)
            lons.append(bbox[0])   # lon_min  = borda ESQUERDA da coluna ix
            lats.append(bbox[3])   # lat_max  = borda SUPERIOR da linha iy
    return lons, lats


def tile_pai(xt, yt, z, niveis=1):
    """Ancestral de um tile `niveis` acima: (xt, yt, z) -> (xt', yt', z-niveis).
    Cada nível divide os índices por 2 (shift). Base do FALLBACK multi-nível
    (técnica do QGIS: fetchOtherResTiles, qgswmsprovider.cpp:716-808)."""
    return xt >> niveis, yt >> niveis, z - niveis


def sub_rect_no_pai(xt, yt, z, zp):
    """Fração (fx, fy, f) do tile ANCESTRAL (nível zp) que corresponde ao tile
    (xt, yt, z): origem relativa (fx, fy) e lado f, todos em [0, 1]. Usada para
    recortar da imagem do ancestral exatamente a área do tile ausente:
        fonte_px = (fx*W, fy*H, f*W, f*H)  na imagem do pai."""
    n = z - zp
    if n <= 0:
        return 0.0, 0.0, 1.0
    f = 1.0 / (1 << n)
    fx = (xt - ((xt >> n) << n)) * f
    fy = (yt - ((yt >> n) << n)) * f
    return fx, fy, f


def ordenar_chebyshev(chaves, centro):
    """Ordena chaves (z, xt, yt) por distância de CHEBYSHEV (max(|dx|,|dy|)) ao
    tile central — o carregamento avança em ANÉIS quadrados do centro para fora,
    "more natural than euclidean/manhattan distance" (QGIS, LessThanTileRequest,
    qgswmsprovider.cpp:101-113). Chaves de outro nível z são reescaladas para o
    nível do centro e penalizadas por |dz| (carrega o nível corrente primeiro)."""
    zc, xc, yc = centro

    def _chave_ordem(c):
        z, xt, yt = c
        dz = z - zc
        if dz > 0:      # tile mais fino: traz para a grade do centro
            xt, yt = xt >> dz, yt >> dz
        elif dz < 0:    # tile mais grosso: leva o centro para a grade dele
            return abs(dz), max(abs(xt - (xc >> -dz)), abs(yt - (yc >> -dz)))
        return abs(dz), max(abs(xt - xc), abs(yt - yc))

    return sorted(chaves, key=_chave_ordem)


# =============================================================================
# 2. PROVEDOR + GERENCIADOR DE TILES (assíncrono, cache 2 níveis)
# =============================================================================

@dataclass(frozen=True)
class Provedor:
    """Fonte de tiles XYZ. `url_template` usa os campos nomeados {z} {x} {y}
    (a ORDEM no texto é a do provedor — Esri é .../{z}/{y}/{x}). `atribuicao` é
    o crédito EXIGIDO em tela. `zoom_max` = maior nível com cobertura CONFIÁVEL
    (acima dele o provedor devolve placeholder); ao passar dele o app faz
    "overzoom" (escala o tile de zoom_max). `hash_indisponivel` = md5 do tile
    placeholder ("dados não disponíveis"), para nunca exibi-lo."""
    nome: str
    url_template: str
    atribuicao: str
    tam_tile: int = 256
    zoom_max: int = 19
    hash_indisponivel: str = ""


# Padrão: Esri World Imagery — sem chave, boa resolução no Brasil, atribuição
# obrigatória (é a camada "ESRI Satellite" do QuickMapServices). zoom_max=17:
# em áreas rurais (ex.: MT) o Esri só cobre até 17; de 18+ devolve o placeholder
# "Map data not yet available" (md5 abaixo). Acima de 17 o app faz overzoom
# (escala o tile de 17) — resolução de sobra para contexto de rede.
PROVEDOR_ESRI = Provedor(
    nome="Esri World Imagery",
    url_template=("https://server.arcgisonline.com/ArcGIS/rest/services/"
                  "World_Imagery/MapServer/tile/{z}/{y}/{x}"),
    atribuicao="Fonte: Esri, Maxar, Earthstar Geographics, e a comunidade GIS",
    zoom_max=17,
    hash_indisponivel="f27d9de7f80c13501f470595e327aa6d",
)

# Google — endpoint XYZ NÃO-OFICIAL (mt.google.com/vt), o MESMO que o QGIS
# QuickMapServices usa no pacote "contribuído". ATENÇÃO: acessar tiles por aqui
# (sem chave/Map Tiles API) VIOLA os Termos de Serviço do Google. Incluído a
# pedido do usuário, rotulado "(não-oficial)" na UI, para uso pessoal/experimental
# — NÃO para produto distribuído. `lyrs=s` = satélite puro; `lyrs=y` = híbrido
# (satélite + ruas/rótulos). Google tem cobertura profunda (zoom_max=20) e não
# devolve o placeholder fixo do Esri (hash_indisponivel vazio).
PROVEDOR_GOOGLE_SAT = Provedor(
    nome="Google Satélite (não-oficial)",
    url_template="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
    atribuicao="Imagens © Google (uso não-oficial)",
    zoom_max=20,
)

PROVEDOR_GOOGLE_HIBRIDO = Provedor(
    nome="Google Híbrido (não-oficial)",
    url_template="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
    atribuicao="Imagens © Google (uso não-oficial)",
    zoom_max=20,
)

# Registro ordenado para o seletor de provedores (estilo QuickMapServices). O
# PRIMEIRO é o padrão — Esri, por ser sem chave e dentro dos termos.
PROVEDORES = [PROVEDOR_ESRI, PROVEDOR_GOOGLE_SAT, PROVEDOR_GOOGLE_HIBRIDO]

_BACKEND_OPENSSL = "openssl"
_BACKEND_SCHANNEL = "schannel"


def garantir_backend_tls():
    """Evita o backend OpenSSL do Qt quando há outra libcrypto no processo.

    `pyproj` (PROJ/curl) e o `hashlib` do CPython carregam a PRÓPRIA cópia de
    OpenSSL, e o Qt é compilado contra outra. Com duas instâncias da biblioteca
    no mesmo processo, o backend OpenSSL do Qt sofre violação de acesso durante
    o handshake TLS e derruba a aplicação — era o que impedia QUALQUER tile de
    ser baixado (o fundo ficava branco ou o processo morria).

    O backend `schannel` é nativo do Windows, não depende de OpenSSL e não
    sofre o conflito. Onde ele não existe (Linux/macOS) nada é alterado.

    Devolve o backend ativo ao final, ou None se o Qt não expuser TLS.
    """
    try:
        from PyQt6.QtNetwork import QSslSocket
    except ImportError:
        return None
    try:
        ativo = QSslSocket.activeBackend()
        if (
            ativo != _BACKEND_OPENSSL
            or _BACKEND_SCHANNEL not in QSslSocket.availableBackends()
        ):
            return ativo
        # setActiveBackend falha se algum handshake já ocorreu; nesse caso
        # mantemos o backend atual em vez de interromper a aplicação.
        if QSslSocket.setActiveBackend(_BACKEND_SCHANNEL):
            return _BACKEND_SCHANNEL
        return ativo
    except Exception:
        return None


class _TransporteTiles(QObject):
    """Executa os pedidos HTTP em um QObject Qt mínimo e isolado."""

    download_concluido = pyqtSignal(object, object, object, bytes, str)
    tile_pronto = pyqtSignal()
    falha_tiles = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._replies = set()
        self._chaves = {}
        self._fechado = False

    def baixar(self, chave, requisicao):
        if self._fechado:
            return None
        reply = self._nam.get(requisicao)
        self._replies.add(reply)
        self._chaves[reply] = chave
        reply.finished.connect(lambda atual=reply: self._finalizar(atual))
        return reply

    def _finalizar(self, reply):
        self._replies.discard(reply)
        chave = self._chaves.pop(reply, ())
        if self._fechado:
            reply.deleteLater()
            return
        status = reply.attribute(
            QNetworkRequest.Attribute.HttpStatusCodeAttribute
        )
        erro = reply.error()
        mensagem = reply.errorString()
        dados = bytes(reply.readAll())
        reply.deleteLater()
        self.download_concluido.emit(chave, erro, status, dados, mensagem)

    def fechar(self):
        if self._fechado:
            return
        self._fechado = True
        for reply in tuple(self._replies):
            try:
                reply.abort()
                reply.deleteLater()
            except RuntimeError:
                pass
        self._replies.clear()
        self._chaves.clear()


class GerenciadorTiles:
    """Baixa e cacheia tiles de UM provedor, com a disciplina de fila do QGIS
    (QgsTileDownloadManager + LessThanTileRequest + repeatTileRequest):

    • FILA PENDENTE com teto de downloads simultâneos (≤ _MAX_EM_VOO): os GET
      não saem todos de uma vez; a fila é drenada em ordem de CHEBYSHEV a partir
      do tile central do frame — os tiles chegam em anéis, do centro para fora.
    • CONJUNTO DE INTERESSE (adaptação do mTileReqNo do QGIS): a cada frame o
      desenho declara quais tiles estão visíveis; pedidos pendentes que já
      saíram da tela (pan rápido) são descartados ao drenar — sem desperdiçar
      banda com tiles obsoletos. Prefetch entra fora do interesse com prioridade
      baixa (fim da fila).
    • FALHAS: HTTP ≥ 400 e placeholder são memoizados (NUNCA re-pedidos — antes,
      um 404 era re-pedido a cada repaint); erro de REDE tem retry até
      _MAX_RETRY_REDE e então memoiza pela sessão (o QGIS re-tenta 3x e não
      memoiza; memoizar é melhoria nossa).
    • CACHE RAM: LRU real POR BYTES (não por contagem — a fraqueza conhecida do
      QCache(256) do QGIS) + cache de disco por arquivo.
    • `tile()` devolve do cache ou enfileira; `tile_do_cache()` NUNCA baixa
      (para o fallback multi-nível, como o fetchOtherResTiles do QGIS)."""

    _LIMITE_MEM_BYTES = 96 * 1024 * 1024   # ~96 MB de tiles decodificados
    _MAX_EM_VOO = 6                        # limite do Qt por host é 6
    _MAX_RETRY_REDE = 3                    # igual ao defaultTileMaxRetry do QGIS

    def __init__(self, provedor=PROVEDOR_ESRI, parent=None):
        self.provedor = provedor
        # Único ponto por onde passam todas as requisições: tarde o bastante
        # para existir QApplication e cedo o bastante para preceder o primeiro
        # handshake TLS.
        self.backend_tls = garantir_backend_tls()
        from collections import OrderedDict
        self._mem = OrderedDict()   # (z,xt,yt) -> QPixmap (LRU: fim = mais novo)
        self._mem_bytes = 0
        self._em_voo = set()        # chaves com GET em andamento (dedup)
        self._pendentes = {}        # chave -> prioridade (0=visível, 1=prefetch)
        self._indisponivel = set()  # 404/placeholder/rede esgotada: não re-pedir
        self._falhas_rede = {}      # chave -> nº de falhas de rede (p/ retry)
        self._interesse = set()     # chaves visíveis no frame corrente
        self._centro = (0, 0, 0)    # tile central (z, xt, yt) p/ Chebyshev
        self._fechado = False
        self._falha_notificada = False
        self._transporte = _TransporteTiles(parent)
        self._transporte.download_concluido.connect(self._ao_terminar)
        self.tile_pronto = self._transporte.tile_pronto
        self.falha_tiles = self._transporte.falha_tiles
        # Mantido para inspeção e testes de encerramento determinístico.
        self._replies = self._transporte._replies
        # Cache de disco em <CacheLocation>/mapa_tiles/<provedor>/z/x/y.png.
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation) or QDir.tempPath()
        seguro = "".join(c if c.isalnum() else "_" for c in provedor.nome)
        self._dir_cache = os.path.join(base, "mapa_tiles", seguro)
        try:
            os.makedirs(self._dir_cache, exist_ok=True)
        except OSError:
            self._dir_cache = None   # sem cache de disco; memória ainda funciona

    # --- API ----------------------------------------------------------------
    def tile(self, z, xt, yt):
        """QPixmap do tile, ou None se ainda não disponível (pedido enfileirado)
        ou definitivamente indisponível (404/placeholder memoizado)."""
        if self._fechado:
            return None
        chave = (z, xt, yt)
        pm = self.tile_do_cache(z, xt, yt)
        if pm is not None:
            return pm
        if chave in self._indisponivel:
            return None
        if chave not in self._em_voo and chave not in self._pendentes:
            self._pendentes[chave] = 0        # prioridade visível
        self._bombear()
        return None

    def tile_do_cache(self, z, xt, yt):
        """SÓ cache (memória → disco), NUNCA dispara download — é a consulta do
        fallback multi-nível (o fetchOtherResTiles do QGIS 'nunca faz request',
        qgswmsprovider.cpp:772)."""
        chave = (z, xt, yt)
        pm = self._mem.get(chave)
        if pm is not None:
            self._mem.move_to_end(chave)      # toque de LRU
            return pm
        caminho = self._caminho_disco(chave)
        if caminho and os.path.exists(caminho):
            pm = QPixmap(caminho)
            if not pm.isNull():
                self._guardar_mem(chave, pm)
                return pm
        return None

    def definir_interesse(self, chaves, centro):
        """Declara o conjunto de tiles VISÍVEIS deste frame e o tile central
        (para a ordenação em anéis). Pedidos pendentes de frames anteriores que
        saíram da tela serão pulados ao drenar a fila."""
        self._interesse = set(chaves)
        self._centro = centro
        self._bombear()

    def prefetch(self, chaves):
        """Enfileira tiles em PRIORIDADE BAIXA só para popular o cache (telas
        vizinhas — os preview jobs do QGIS). Nunca rouba a vez dos visíveis."""
        if self._fechado:
            return
        for chave in chaves:
            if (chave in self._mem or chave in self._em_voo
                    or chave in self._indisponivel or chave in self._pendentes):
                continue
            caminho = self._caminho_disco(chave)
            if caminho and os.path.exists(caminho):
                continue                      # já está no disco
            self._pendentes[chave] = 1
        self._bombear()

    def limpar_memoria(self):
        """Solta o cache de memória (o de disco permanece)."""
        self._mem.clear()
        self._mem_bytes = 0

    def fechar(self):
        """Cancela downloads e libera os caches voláteis de forma idempotente."""
        if self._fechado:
            return
        self._fechado = True
        self._pendentes.clear()
        self._interesse.clear()
        try:
            self._transporte.download_concluido.disconnect(self._ao_terminar)
        except (RuntimeError, TypeError):
            pass
        self._transporte.fechar()
        self._em_voo.clear()
        self.limpar_memoria()

    def deleteLater(self):  # noqa: N802
        """Compatibilidade com o ciclo de vida usado pelo ``DiagramView``."""
        self._transporte.deleteLater()

    # --- Fila ----------------------------------------------------------------
    def _bombear(self):
        """Drena a fila pendente respeitando o teto de vôo. Ordem: prioridade
        (visível antes de prefetch) e distância de Chebyshev ao tile central.
        Pedidos visíveis que saíram do interesse são DESCARTADOS aqui."""
        if self._fechado:
            return
        while self._pendentes and len(self._em_voo) < self._MAX_EM_VOO:
            ordem = ordenar_chebyshev(list(self._pendentes), self._centro)
            ordem.sort(key=lambda c: self._pendentes[c])   # estável: prio 1º
            chave = ordem[0]
            prio = self._pendentes.pop(chave)
            if prio == 0 and self._interesse and chave not in self._interesse:
                continue   # saiu da tela antes de baixar: não desperdiça banda
            if chave in self._indisponivel or chave in self._mem:
                continue
            self._baixar(chave)

    # --- Interno ------------------------------------------------------------
    def _caminho_disco(self, chave):
        if not self._dir_cache:
            return None
        z, xt, yt = chave
        return os.path.join(self._dir_cache, str(z), str(xt), f"{yt}.png")

    def _url(self, chave):
        z, xt, yt = chave
        return self.provedor.url_template.format(z=z, x=xt, y=yt)

    def _baixar(self, chave):
        if self._fechado:
            return
        self._em_voo.add(chave)
        req = QNetworkRequest(QUrl(self._url(chave)))
        # Alguns servidores de tile rejeitam User-Agent vazio (OSM/Esri exigem
        # identificação — mesmo motivo do X-Requested-With do QGIS).
        req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader,
                      "CircuitViewer/0.1")
        req.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        self._transporte.baixar(chave, req)

    def _ao_terminar(self, chave, erro, status, dados, _mensagem=""):
        chave = tuple(chave or ())
        self._em_voo.discard(chave)
        try:
            if erro != QNetworkReply.NetworkError.NoError:
                if len(chave) != 3:
                    return
                if status is not None and int(status) >= 400:
                    # 404/403/500: PERMANENTE para este tile — memoiza e nunca
                    # re-pede (o QGIS não memoiza e sofre com isso; retry só
                    # vale para falha de REDE, qgswmsprovider.cpp:5139-5144).
                    self._indisponivel.add(chave)
                    self._notificar_falha(f"HTTP {int(status)}")
                else:
                    # Falha de rede/timeout: retry com teto, como o QGIS (3x).
                    n = self._falhas_rede.get(chave, 0) + 1
                    self._falhas_rede[chave] = n
                    if n < self._MAX_RETRY_REDE:
                        self._pendentes.setdefault(chave, 0)
                    else:
                        self._indisponivel.add(chave)   # sessão: desiste
                        self._notificar_falha(_mensagem or "falha de rede")
                return
            # Placeholder "dados não disponíveis" do provedor: memoiza e NÃO
            # desenha (melhor vazio do que o texto "Map data not yet available").
            # Não é falha de acesso — é ausência de cobertura, resolvida pelo
            # overzoom — então não notifica a interface.
            assinatura = self.provedor.hash_indisponivel
            if assinatura and hashlib.md5(dados).hexdigest() == assinatura:
                if len(chave) == 3:
                    self._indisponivel.add(chave)
                return
            pm = QPixmap()
            if not pm.loadFromData(dados):
                return
            if len(chave) == 3:
                self._falhas_rede.pop(chave, None)
                self._guardar_mem(chave, pm)
                self._gravar_disco(chave, dados)
                self.tile_pronto.emit()   # pede repaint do viewport
        finally:
            if not self._fechado:
                self._bombear()           # liberou 1 slot: puxa o próximo

    def _notificar_falha(self, motivo):
        """Avisa a interface UMA vez por gerenciador que os tiles não chegam.

        Sem isso, um 404/403 ou uma falha de rede eram memoizados em silêncio e
        o usuário via apenas um fundo branco, sem qualquer pista do motivo.
        """
        if self._falha_notificada or self._fechado:
            return
        self._falha_notificada = True
        self._transporte.falha_tiles.emit(str(motivo))

    def _guardar_mem(self, chave, pm):
        """LRU por BYTES: expulsa os mais antigos até caber. Estimativa de
        custo = w*h*4 (ARGB32) — QPixmap não expõe sizeInBytes."""
        custo = pm.width() * pm.height() * 4
        antigo = self._mem.pop(chave, None)
        if antigo is not None:
            self._mem_bytes -= antigo.width() * antigo.height() * 4
        while self._mem and self._mem_bytes + custo > self._LIMITE_MEM_BYTES:
            _, velho = self._mem.popitem(last=False)   # o menos recente
            self._mem_bytes -= velho.width() * velho.height() * 4
        self._mem[chave] = pm
        self._mem_bytes += custo

    def _gravar_disco(self, chave, dados):
        caminho = self._caminho_disco(chave)
        if not caminho:
            return
        try:
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            with open(caminho, "wb") as f:
                f.write(dados)
        except OSError:
            pass  # cache de disco é best-effort
