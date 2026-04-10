const { app, BrowserWindow, ipcMain, Menu } = require('electron');
const path = require('path');

let mainWindow;
let settings = {
  apiKey: 'sk-Xk7kOHpIpZkLutwnyxHpRO9jn4ZwyPaS',
  baseURL: 'http://localhost:8317/v1',
  model: 'claude-opus-4-5-20251101',
  maxTokens: 4096
};

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 800,
    height: 600,
    minWidth: 500,
    minHeight: 400,
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  mainWindow.loadFile('index.html');

  // Build menu
  const template = [
    {
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' }
      ]
    },
    {
      label: 'File',
      submenu: [
        {
          label: 'New Chat',
          accelerator: 'CmdOrCtrl+N',
          click: () => mainWindow.webContents.send('new-chat')
        }
      ]
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' }
      ]
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' }
      ]
    }
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

// IPC Handlers
ipcMain.handle('get-settings', () => settings);

ipcMain.handle('save-settings', (event, newSettings) => {
  settings = { ...settings, ...newSettings };
  return settings;
});

ipcMain.handle('fetch-models', async () => {
  try {
    const response = await fetch(`${settings.baseURL}/models`, {
      headers: {
        'x-api-key': settings.apiKey,
        'anthropic-version': '2023-06-01'
      }
    });

    if (!response.ok) return [];

    const data = await response.json();
    return data.data?.map(m => m.id) || [];
  } catch (error) {
    console.error('Error fetching models:', error);
    return [];
  }
});

// Streaming message handler
ipcMain.on('send-message', async (event, { content, model }) => {
  const useModel = model || settings.model;

  try {
    const response = await fetch(`${settings.baseURL}/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': settings.apiKey,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: useModel,
        max_tokens: settings.maxTokens,
        messages: [{ role: 'user', content }],
        stream: true
      })
    });

    if (!response.ok) {
      const errorText = await response.text();
      if (response.status === 400) {
        event.reply('stream-error', { type: 'model_not_available', model: useModel });
      } else {
        event.reply('stream-error', { type: 'http', status: response.status, message: errorText });
      }
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') continue;

          try {
            const event_data = JSON.parse(data);
            if (event_data.type === 'content_block_delta') {
              const text = event_data.delta?.text || '';
              if (text) {
                event.reply('stream-chunk', text);
              }
            } else if (event_data.type === 'message_stop') {
              break;
            }
          } catch (e) {
            // Ignore parse errors
          }
        }
      }
    }

    event.reply('stream-end');
  } catch (error) {
    event.reply('stream-error', { type: 'network', message: error.message });
  }
});
