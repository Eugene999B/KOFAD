"use strict";
const path = require("node:path");
const { app, BrowserWindow, dialog, shell, session, Menu } = require("electron");
const { autoUpdater } = require("electron-updater");
const { profileFor, isInternal, isAllowedExternal } = require("./profile.cjs");

const profile = profileFor(process.env.KOFAD_NATIVE_CHANNEL || "customer");
app.setName(profile.productName);
app.setPath("userData", path.join(app.getPath("appData"), profile.userData));
app.enableSandbox();
let mainWindow;
let updateDownloaded = false;

function openExternalIfApproved(url) {
  if (isAllowedExternal(url)) void shell.openExternal(url);
}
function createWindow() {
  mainWindow = new BrowserWindow({
    width: profile.productName === "KOFAD Staff" ? 1450 : 1180,
    height: 850, minWidth: 380, minHeight: 590,
    title: profile.productName,
    backgroundColor: "#10374c",
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      sandbox: true, contextIsolation: true, nodeIntegration: false,
      webviewTag: false, allowRunningInsecureContent: false,
      spellcheck: true,
    },
  });
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isInternal(profile, url)) {
      void mainWindow.loadURL(url);
    } else {
      openExternalIfApproved(url);
    }
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isInternal(profile, url)) {
      event.preventDefault();
      openExternalIfApproved(url);
    }
  });
  mainWindow.webContents.on("will-redirect", (event, url) => {
    if (!isInternal(profile, url)) {
      event.preventDefault();
      openExternalIfApproved(url);
    }
  });
  // No browser-to-Node bridge, no privileged IPC, and no automatic permission grants.
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  void mainWindow.loadURL(profile.startUrl).catch(() => {
    dialog.showMessageBoxSync(mainWindow, {
      type: "warning", title: "Connection unavailable",
      message: "Unable to reach the official KOFAD service.",
      detail: "Check your connection. No business records were changed.",
    });
  });
  const menus = [
    { label: "Application", submenu: [
      { label: "Reload KOFAD", accelerator: "CmdOrCtrl+R", click: () => mainWindow.reload() },
      { label: "Open in browser", click: () => void shell.openExternal(profile.startUrl) },
      { label: "Check for updates", click: () => void checkForUpdates(true) },
      { type: "separator" }, { role: "quit" },
    ] },
    { label: "Edit", submenu: [{ role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: "View", submenu: [{ role: "zoomIn" }, { role: "zoomOut" }, { role: "resetZoom" }] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(menus));
}
async function checkForUpdates(manual = false) {
  if (!app.isPackaged) {
    if (manual) dialog.showMessageBoxSync({ type: "info", message: "Updates are enabled on signed published installers only." });
    return;
  }
  try {
    await autoUpdater.checkForUpdates();
    if (manual && !updateDownloaded) {
      // The updater may emit update-not-available asynchronously.
    }
  } catch (_error) {
    if (manual && mainWindow && !mainWindow.isDestroyed()) {
      void dialog.showMessageBox(mainWindow, {
        type: "warning", title: "Update check unavailable",
        message: "Could not check the trusted update server.",
        detail: "Your current application remains installed and functional.",
      });
    }
  }
}
function configureUpdater() {
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.on("update-available", async (info) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    const answer = await dialog.showMessageBox(mainWindow, {
      type: "info", title: "KOFAD update available",
      message: `Version ${info.version} is available.`,
      detail: "Download the signed update? Your app will not restart during ongoing work.",
      buttons: ["Download update", "Later"], defaultId: 1, cancelId: 1, noLink: true,
    });
    if (answer.response === 0) void autoUpdater.downloadUpdate().catch(() => {});
  });
  autoUpdater.on("update-downloaded", async () => {
    updateDownloaded = true;
    if (!mainWindow || mainWindow.isDestroyed()) return;
    const answer = await dialog.showMessageBox(mainWindow, {
      type: "question", title: "KOFAD update ready",
      message: "The verified update has downloaded.",
      detail: "Save or finish any sales and forms before installing. The application will close and restart.",
      buttons: ["Install and restart", "Later"], defaultId: 1, cancelId: 1, noLink: true,
    });
    if (answer.response === 0) autoUpdater.quitAndInstall();
  });
}
app.whenReady().then(() => {
  createWindow();
  configureUpdater();
  if (app.isPackaged) {
    setTimeout(() => { void checkForUpdates(); }, 30000);
    setInterval(() => { void checkForUpdates(); }, 6 * 60 * 60 * 1000);
  }
});
app.on("window-all-closed", () => app.quit());
