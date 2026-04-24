# rf — Android Development CLI

> Automação inteligente para desenvolvimento Android.

```
      /\        /\
     /  \      /  \
 ╔═══════════════════════╗
 ║  ◉               ◉  ║
 ╠═══════════════════════╣
 ║  ▸ rf  ·  leafar      ║
 ║  android  ai  agent   ║
 ╚═══════════════════════╝
   powered by Rafael Alves
```

## Instalação

```bash
# Via script (recomendado — instala tudo automaticamente)
curl -fsSL https://raw.githubusercontent.com/faelmg18/leafar/main/install.sh | sh

# Via pipx (ambiente isolado)
pipx install rf-leafar

# Via pip
pip install rf-leafar
```

**Requisitos:** Python 3.10+, Node.js, Android SDK (`adb` no PATH)

## Primeiros passos

```bash
cd /seu-projeto-android
rf init    # configura o projeto automaticamente
rf chat    # abre o terminal de desenvolvimento
```

## Comandos

```bash
rf init          # Detecta package, configura ferramentas, gera CLAUDE.md
rf chat          # Abre o terminal de desenvolvimento
rf ask "..."     # Executa uma tarefa diretamente
rf run           # Build + instala + lança o app no emulador
rf screenshot    # Captura a tela do emulador
rf navigate      # Navega para uma tela do app
rf debug         # Depura um problema no app
```

## O que o rf faz

Conecta seu projeto Android a um conjunto de ferramentas de automação:

| Categoria | Ferramentas |
|---|---|
| **Emulador** | screenshot, tap, swipe, input de texto, teclas |
| **ADB** | comandos diretos, hierarquia de UI, activity atual |
| **App** | launch, stop, install APK |
| **Código** | leitura, escrita e busca em arquivos |
| **Gradle** | build, install, testes |

## Integrações

Configure dentro do terminal conforme precisar:

- 🐙 **GitHub** — `/github-login`
- ☁️ **Azure DevOps** — `/azure-login`
- 🎨 **Figma** — via plugin

## Licença

MIT © Rafael Alves
