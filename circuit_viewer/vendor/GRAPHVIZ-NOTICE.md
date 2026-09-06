# Graphviz portátil

Este aplicativo redistribui o pacote oficial **Graphviz 15.1.1 para Windows
64 bits** sem modificações, no diretório `graphviz-15.1.1-win64`.

- Arquivo de origem: `windows_10_cmake_Release_Graphviz-15.1.1-win64.zip`
- URL: <https://gitlab.com/api/v4/projects/4207231/packages/generic/graphviz-releases/15.1.1/windows_10_cmake_Release_Graphviz-15.1.1-win64.zip>
- SHA-256: `e8256ef077e601d9f284378d96cd17faa7910832cf6bb85c43005e66ec2f255e`
- Código-fonte correspondente: <https://gitlab.com/graphviz/graphviz/-/tree/15.1.1>
- Licença: Eclipse Public License 2.0, reproduzida em
  `GRAPHVIZ-EPL-2.0.txt`.

A aplicação invoca exclusivamente `bin/dot.exe` para calcular posições,
splines e âncoras em JSON. O Graphviz não renderiza SVG, PNG ou qualquer outro
conteúdo visual exibido ao usuário; a renderização continua integralmente no
Qt.
