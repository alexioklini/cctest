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

  // Read one file off disk into the same pending-file shape the renderer
  // pushes onto state._pendingFiles. Returns { error } on failure so the
  // caller can skip it without aborting a whole folder.
  function readFileEntry(filePath) {
    try {
      const stat = fs.statSync(filePath);
      if (stat.size > 50 * 1024 * 1024) return { error: 'File too large (>50MB)' };
      const data = fs.readFileSync(filePath);
      const ext = path.extname(filePath).toLowerCase();
      const type = mimeMap[ext] || 'application/octet-stream';
      const isImage = type.startsWith('image/');
      return {
        name: path.basename(filePath),
        type,
        data: data.toString('base64'),
        encoding: 'base64',
        preview: isImage ? `data:${type};base64,${data.toString('base64')}` : null,
      };
    } catch (e) {
      return { error: e.message };
    }
  }

  ipcMain.handle('read-dropped-file', async (_event, filePath) => readFileEntry(filePath));

  // Walk a dropped path: a folder is recursed into (depth-first), returning
  // every readable file under it; a plain file returns a single-element
  // array. Mirrors the browser webkitGetAsEntry walk so dragging a folder
  // attaches its files recursively in both the desktop app and the browser.
  ipcMain.handle('read-dropped-folder', async (_event, rootPath) => {
    const out = [];
    // relPath is rooted at the dropped folder's basename so the renderer can
    // mirror the structure into project groups (e.g. "MyFolder/sub/x.pdf").
    // A dropped single file gets a bare basename. Older renderers ignore it.
    const rootDir = path.dirname(rootPath);
    const walk = (p) => {
      let stat;
      try { stat = fs.statSync(p); } catch { return; }
      if (stat.isDirectory()) {
        let names;
        try { names = fs.readdirSync(p); } catch { return; }
        // Skip dotfiles/dirs (.git, .DS_Store, …) — dragging a project
        // folder should attach its content, not VCS/OS cruft.
        for (const name of names) {
          if (name.startsWith('.')) continue;
          walk(path.join(p, name));
        }
      } else if (stat.isFile()) {
        const entry = readFileEntry(p);
        if (entry && !entry.error) {
          entry.relPath = path.relative(rootDir, p).split(path.sep).join('/');
          out.push(entry);
        }
      }
    };
    walk(rootPath);
    return out;
  });
}

module.exports = { register };
