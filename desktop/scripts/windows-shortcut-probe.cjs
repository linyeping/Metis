const fs = require('node:fs')
const { app, shell } = require('electron')

const path = require('node:path')

const [, , outputPath, shortcutPath, targetPath, iconPath, appUserModelId] = process.argv

if (!outputPath || !shortcutPath || !targetPath || !iconPath || !appUserModelId) {
  process.stderr.write('windows-shortcut-probe requires output, shortcut, target, icon, and AppUserModelID arguments\n')
  process.exit(2)
}

app.setAppUserModelId(appUserModelId)

app.whenReady().then(() => {
  try {
    fs.mkdirSync(path.dirname(outputPath), { recursive: true })
    fs.rmSync(shortcutPath, { force: true })
    const written = shell.writeShortcutLink(shortcutPath, 'create', {
      target: targetPath,
      icon: iconPath,
      iconIndex: 0,
      description: 'Metis Windows identity build verification',
      appUserModelId
    })
    if (!written) throw new Error('Electron did not create the identity probe shortcut')
    const details = shell.readShortcutLink(shortcutPath)
    fs.writeFileSync(outputPath, `${JSON.stringify({ ok: true, details })}\n`, 'utf8')
    fs.rmSync(shortcutPath, { force: true })
    app.quit()
  } catch (error) {
    fs.rmSync(shortcutPath, { force: true })
    fs.writeFileSync(outputPath, `${JSON.stringify({ ok: false, error: error?.message || String(error) })}\n`, 'utf8')
    process.stderr.write(`${error?.stack || error}\n`)
    app.exit(1)
  }
})
