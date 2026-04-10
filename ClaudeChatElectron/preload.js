const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  // Settings
  getSettings: () => ipcRenderer.invoke('get-settings'),
  saveSettings: (settings) => ipcRenderer.invoke('save-settings', settings),

  // Models
  fetchModels: () => ipcRenderer.invoke('fetch-models'),

  // Messaging
  sendMessage: (content, model) => ipcRenderer.send('send-message', { content, model }),

  // Stream listeners
  onStreamChunk: (callback) => ipcRenderer.on('stream-chunk', (_, text) => callback(text)),
  onStreamEnd: (callback) => ipcRenderer.on('stream-end', () => callback()),
  onStreamError: (callback) => ipcRenderer.on('stream-error', (_, error) => callback(error)),

  // Menu events
  onNewChat: (callback) => ipcRenderer.on('new-chat', () => callback()),

  // Cleanup
  removeAllListeners: () => {
    ipcRenderer.removeAllListeners('stream-chunk');
    ipcRenderer.removeAllListeners('stream-end');
    ipcRenderer.removeAllListeners('stream-error');
  }
});
