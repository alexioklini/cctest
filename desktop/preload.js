const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // Server connection
  getServerUrl: () => ipcRenderer.invoke('get-server-url'),
  setServerUrl: (url) => ipcRenderer.invoke('set-server-url', url),
  checkServer: (url) => ipcRenderer.invoke('check-server', url),

  // CORS-free web fetch via Node.js main process
  webFetch: (opts) => ipcRenderer.invoke('web-fetch', opts),

  // CORS-free Exa search via Node.js main process
  exaSearch: (opts) => ipcRenderer.invoke('exa-search', opts),

  // Generic fetch for LLM proxy (non-streaming)
  proxyFetch: (opts) => ipcRenderer.invoke('proxy-fetch', opts),

  // Streaming fetch for LLM proxy
  proxyFetchStream: (opts) => ipcRenderer.send('proxy-fetch-stream', opts),
  onStreamChunk: (cb) => ipcRenderer.on('proxy-fetch-stream-chunk', (_e, chunk) => cb(chunk)),
  onStreamEnd: (cb) => ipcRenderer.on('proxy-fetch-stream-end', () => cb()),
  onStreamError: (cb) => ipcRenderer.on('proxy-fetch-stream-error', (_e, msg) => cb(msg)),
  removeStreamListeners: () => {
    ipcRenderer.removeAllListeners('proxy-fetch-stream-chunk');
    ipcRenderer.removeAllListeners('proxy-fetch-stream-end');
    ipcRenderer.removeAllListeners('proxy-fetch-stream-error');
  },

  // Notifications
  showNotification: (opts) => ipcRenderer.invoke('show-notification', opts),

  // Auto-launch
  getAutoLaunch: () => ipcRenderer.invoke('get-auto-launch'),
  setAutoLaunch: (enabled) => ipcRenderer.invoke('set-auto-launch', enabled),

  // Clipboard image
  clipboardReadImage: () => ipcRenderer.invoke('clipboard-read-image'),

  // File drag & drop (read native file path)
  readDroppedFile: (filePath) => ipcRenderer.invoke('read-dropped-file', filePath),

  // Menu events
  onNewChat: (cb) => ipcRenderer.on('menu-new-chat', () => cb()),

  // Platform info
  platform: process.platform,
});
