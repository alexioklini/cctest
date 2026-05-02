/* ─── Auto-update (Squirrel via update.electronjs.org) ──────────────── */
const { app, autoUpdater, dialog } = require('electron');

function setup(getMainWindow) {
  if (!app.isPackaged) {
    console.log('[autoUpdater] Skipped — running in dev mode. Install from .dmg/.exe to test auto-update.');
    return;
  }

  const feedURL = `https://update.electronjs.org/alexioklini/cctest/${process.platform}-${process.arch}/${app.getVersion()}`;
  console.log('[autoUpdater] Feed URL:', feedURL);

  try {
    autoUpdater.setFeedURL({ url: feedURL });
  } catch (e) {
    console.error('[autoUpdater] setFeedURL failed:', e.message);
    return;
  }

  autoUpdater.on('checking-for-update', () => {
    console.log('[autoUpdater] Checking for update...');
  });

  autoUpdater.on('error', (err) => {
    console.error('[autoUpdater] Error:', err.message);
  });

  autoUpdater.on('update-available', () => {
    console.log('[autoUpdater] Update available, downloading...');
  });

  autoUpdater.on('update-not-available', () => {
    console.log('[autoUpdater] Up to date (v' + app.getVersion() + ')');
  });

  autoUpdater.on('update-downloaded', (_event, releaseNotes, releaseName) => {
    dialog.showMessageBox(getMainWindow(), {
      type: 'info',
      title: 'Update Ready',
      message: `Version ${releaseName || 'new'} has been downloaded.`,
      detail: 'The app will restart to apply the update.',
      buttons: ['Restart Now', 'Later'],
      defaultId: 0,
    }).then(({ response }) => {
      if (response === 0) autoUpdater.quitAndInstall();
    });
  });

  autoUpdater.checkForUpdates();
  setInterval(() => autoUpdater.checkForUpdates(), 4 * 60 * 60 * 1000);
}

module.exports = { setup };
