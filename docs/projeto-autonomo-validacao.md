# Projeto autônomo — implementação e validação

Verificação concluída em 9 de setembro de 2026. Projeto de estudos em memória;
arquivos Access são fontes de entrada, sem sincronização ou escrita no banco.

## Comportamento implementado

- Estado canônico com UUID de projeto/equipamento, revisão, proveniência por
  campo, retratos de comparação e histórico. Modelos colunares são projeções.
- Importar e Adicionar convergem para revisão no projeto atual. Novo projeto
  reinicia o estudo. A última operação confirmada pode ser desfeita.
- Revisão obrigatória de alimentadores existentes, inclusive sem diferenças;
  Manter, Atualizar por comparação de três versões ou Substituir com exclusões
  previstas. Ausência fora do escopo fornecido não significa exclusão.
- Equipamentos manuais/compartilhados são preservados na substituição.
  Exclusões com referências quebradas são recusadas antes da instalação.
- Edições de regulador, patamares de circuito e estado atual das chaves passam
  pelo núcleo. Adicionar um banco não restaura valores antigos da origem.
- Conectividade física e operacional distintas. Conexões pendentes podem ser
  resolvidas ao carregar a outra ponta, sem reler o primeiro banco.
- Workers identificam operação/revisão. Cancelamento e sinais obsoletos não
  instalam candidatos. Falhas de instalação restauram os modelos anteriores,
  incluindo modelos opcionais ausentes.
- Consulta de origem/histórico e separação entre desvincular arquivos e excluir
  equipamentos. Importações reconhecidas após remoção preservam UUIDs.
- Fluxo de potência e master de fonte única bloqueiam componentes com raízes de
  alimentadores condutoramente unidas. Solução elétrica multifonte é posterior.
- Entradas de estudos possuem identificação de revisão e assinatura das
  configurações/bibliotecas. Exportações comuns incluem `estudo.json`.

## Evidências de regressão

Rodada final conjunta: **885 testes e 169 subtestes aprovados**, em 48,31 s.
Abrange núcleo, importador/worker, interface, cadastro, composição, exportação,
fluxo de potência com motor simulado, blocos, ramais e ciclo de vida dos sinais.
O XML JUnit foi salvo junto aos artefatos da auditoria.

A execução ampliada anterior, excluindo apenas os dois módulos de satélite/TLS,
aprovou 1.741 testes principais e 294 subtestes. Nela, 16 verificações de sinais
atrasados ainda simulavam `QObject.sender()`. Foram adaptadas para emitir sinais
pelo despachante efetivamente usado pela aplicação; todas passaram na rodada
final. As contagens das rodadas não devem ser somadas.

Os testes de persistência falharam no sandbox por falta de acesso ao Registro do
Windows. A execução no namespace isolado `CircuitViewerTests`, fora do sandbox,
aprovou os 12 testes e 3 subtestes de persistência.

### Nove achados da auditoria

| Achado | Tratamento e evidência |
|---|---|
| Interligações omitidas na exportação | Exportação percorre chaves fisicamente internas ao escopo; fronteiras e dados incompatíveis têm diagnóstico. Banco real e chaves simuladas abertas/fechadas verificados. |
| Referências cruzadas entre bancos independentes | Referências ausentes são isoladas do domínio de outras fontes; teste impede usar cabo de outro banco com o mesmo ID. |
| Correspondências persistidas eliminavam interligações | Seleção acumulada corrigida no adaptador; núcleo guarda conexões pendentes. Regressão com correspondência manual persistida e segunda seleção aprovada. |
| Cabos antigos sobreviviam a uma instalação parcial | Setter de cabos é chamado também com `None`; testes verificam a ausência após instalação/restauração. |
| Cancelamento após o sinal de sucesso | Estado de cancelamento é revalidado imediatamente antes do commit; projeto anterior preservado. |
| Falha em setter deixava instalação parcial | Instalação restaura a cadeia anterior; falha controlada confirma modelos e projeto anteriores, inclusive opcionais ausentes. |
| Falha nativa conjunta do Qt | Um único `QApplication` pertence à sessão de testes. Despacho captura a operação sem consultar `sender()` após teardown. Rodada conjunta final sem a violação de acesso reproduzida na auditoria. |
| Terceira fonte duplicava variante de cabo | Deduplicação por ID e conteúdo, incluindo variantes já qualificadas; regressão A:X → B:Y → C:Y conserva duas definições. |
| Alocações parciais desapareciam sem aviso | Ausência é diagnosticada tanto entre redes independentes como entre arquivos vinculados à mesma rede. Registros disponíveis ficam preservados no cadastro. |

Também foram verificados: reimportação sem diferenças, mudanças locais/externas
e simultâneas, exclusão de cabo ainda referenciado, equipamentos manuais,
desvinculação, remoção/undo/reimportação, identidade após cópia do banco, agendas
editadas, estados desconhecidos, metadados de entrada e inspeção da proveniência.

## Banco representativo

Banco: `rede.mdb`, lido pelo adaptador Access em modo somente leitura. A conexão
foi fechada antes da revisão, composição e exportação. A assinatura do arquivo
foi conferida ao final e permaneceu igual. Nenhuma senha foi gravada no relatório.

| Verificação | Resultado |
|---|---:|
| Alimentadores | 26 |
| Barras | 16.323 |
| Trechos | 16.405 |
| Chaves no projeto | 1.472 |
| Interligações magenta | **90** |
| Chaves exportadas | 1.382 |
| Chaves recusadas com diagnóstico individual | 90 |
| Conexões pendentes após carregar todos | 0 |

As 90 interligações têm **`FASES2 = 0`**, sem correspondência em `fases2.json`.
Continuam existindo no projeto e no grafo. Agora são examinadas pelo exportador
e recebem motivo explícito, em vez de serem omitidas por ausência de dono
nominal. Não foi inventada uma configuração de fases para emití-las.

Importações 13 + 13 e 9 + 9 + 8 em ordem inversa produziram os mesmos registros,
atributos e referências da importação conjunta. Reimportar todos preservou os
UUIDs. Uma divergência inicial revelou registros vazios de patamares que só
existiam em uma das rotas; a captura agora representa somente patamares reais.
Os 40.195 registros técnicos do núcleo não incluem esses antigos marcadores
vazios. As contagens de equipamentos físicos permaneceram iguais.

Reprodução: `benchmarks/validate_project_access.py CAMINHO --output relatorio.json`.
A credencial é obtida do cofre Windows já configurado; o script não aceita senha
pela linha de comando nem altera o MDB.

## Interface e limites

Revisão de alimentadores, conflitos por campo e proveniência foram renderizadas
nos temas claro/escuro em escalas de 100% e 150%. Campos, valores, contraste e
botões foram conferidos. As imagens estão junto aos artefatos da auditoria.

- Não há arquivo de projeto, SCADA, sincronização automática ou implementação
  integral de CIM. Encerrar a aplicação encerra o projeto em memória.
- A solução elétrica acoplada multifonte continua bloqueada; os testes não
  constituem validação matemática do motor elétrico.
- Os dois módulos adicionais de satélite/TLS passaram em execução separada
  (31 testes e 3 subtestes), mas o runtime emitiu diagnóstico nativo
  `0xC0000139` ao carregar o backend TLS do Qt. Isso é distinto da violação de
  acesso `0xC0000005` corrigida na auditoria. A questão de compatibilidade de DLL
  permanece classificada separadamente; nenhuma alteração experimental do TLS
  foi mantida nesta entrega.
- A restauração após falha foi exercitada com exceção controlada em setter.
  Testes aprovados demonstram os cenários executados, sem garantir ausência
  absoluta de falhas em qualquer ambiente ou banco externo.

As alterações locais preexistentes e os bancos externos foram preservados.
