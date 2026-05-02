/* ─── System IPC: notifications, auto-launch, clipboard, file drop ──── */
const { ipcMain, Notification, clipboard, app } = require('electron');
const path = require('path');
const fs = require('fs');

function register(getMainWindow) {
  ipcMain.handle('show-notification', (_event, { title, body, silent }) => {
    if (!Notification.isSupported()) return false;
    const notif = new Notification({
      title: title || 'Brain Agent',
      body: body || '',
      silent: silent ?? false,
      icon: path.join(__dirname, 'icons', 'icon.png'),
    });
    notif.on('click', () => {
      const win = getMainWindow();
      win?.show();
      win?.focus();
    });
    notif.show();
    return true;
  });

  ipcMain.handle('get-auto-launch', () => {
    return app.getLoginItemSettings().openAtLogin;
  });

  ipcMain.handle('set-auto-launch', (_event, enabled) => {
    app.setLoginItemSettings({ openAtLogin: enabled });
    return app.getLoginItemSettings().openAtLogin;
  });

  ipcMain.handle('clipboard-read-image', () => {
    const img = clipboard.readImage();
    if (img.isEmpty()) return null;
    const png = img.toPNG();
    return { data: png.toString('base64'), type: 'image/png', width: img.getSize().width, height: img.getSize().height };
  });

  ipcMain.handle('read-dropped-file', async (_event, filePath) => {
    try {
      const stat = fs.statSync(filePath);
      if (stat.size > 50 * 1024 * 1024) return { error: 'File too large (>50MB)' };
      const data = fs.readFileSync(filePath);
      const name = path.basename(filePath);
      const ext = path.extname(filePath).toLowerCase();
      const mimeMap = {
        '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
        '.webp': 'image/webp', '.svg': 'image/svg+xml', '.bmp': 'image/bmp',
        '.pdf': 'application/pdf', '.doc': 'application/msword',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        '.csv': 'text/csv', '.txt': 'text/plain', '.md': 'text/markdown',
        '.json': 'application/json', '.xml': 'application/xml',
        '.py': 'text/x-python', '.js': 'text/javascript', '.ts': 'text/typescript',
        '.html': 'text/html', '.css': 'text/css',
      };
      const type = mimeMap[ext] || 'application/octet-stream';
      const isImage = type.startsWith('image/');
      return {
        name,
        type,
        data: data.toString('base64'),
        encoding: 'base64',
        preview: isImage ? `data:${type};base64,${data.toString('base64')}` : null,
      };
    } catch (e) {
      return { error: e.message };
    }
  });
}

module.exports = { register };
