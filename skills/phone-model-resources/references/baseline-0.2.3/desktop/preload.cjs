const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('helper', {
  call: (action, data) => ipcRenderer.invoke('helper', action, data),
  onReady: callback => ipcRenderer.on('service-ready', () => callback()),
  onFailure: callback => ipcRenderer.on('service-failed', () => callback())
});
