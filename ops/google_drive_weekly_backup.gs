/**
 * KOFAD IMPEX ENTERPRISE — weekly Google Drive backup
 *
 * Paste this file in a new, PRIVATE script at https://script.google.com/
 * while logged in as kofadimpexenterprise@gmail.com.
 * Set Script Properties (Project Settings > Script properties):
 *   KOFAD_BACKUP_SIGNING_KEY = same long random secret as Railway's
 *                              KOFAD_GDRIVE_BACKUP_HMAC_KEY
 *   KOFAD_BACKUP_FOLDER_ID   = 15HUuUOLo6oDqP-RKd2WuKb4flm1EsNo9
 * Run installWeeklyKofadTrigger() once, grant permissions, then run
 * weeklyKofadBackup() manually ONCE to verify the first restore-capable file.
 *
 * DO NOT put the encryption passphrase or an admin password in this script.
 * The encryption passphrase stays on the KOFAD server and in a secure OFFLINE
 * recovery record controlled by the owner (separate from this Google account).
 */
const KOFAD_BACKUP_URL = 'https://staff.kofadimpex.com/settings/backup/google/export/';
const KOFAD_BACKUP_TZ = 'Africa/Accra';
const KOFAD_BACKUP_PREFIX = 'KOFAD-FULL-BACKUP-';
const KOFAD_BACKUP_SUFFIX = '.kofad.enc';
const KOFAD_BACKUP_KEEP = 4;
const KOFAD_BACKUP_MAX_BYTES = 40 * 1024 * 1024;
const KOFAD_BACKUP_NOTIFY = 'kofadimpexenterprise@gmail.com';
const KOFAD_ENCRYPTED_MAGIC = 'KOFAD-ENCRYPTED-BACKUP-V1\n';

function kofadHex_(byteArray) {
  return byteArray.map(function (b) {
    return ('0' + (b & 255).toString(16)).slice(-2);
  }).join('');
}

function kofadSettings_() {
  const props = PropertiesService.getScriptProperties();
  const key = props.getProperty('KOFAD_BACKUP_SIGNING_KEY') || '';
  const folderId = props.getProperty('KOFAD_BACKUP_FOLDER_ID') || '';
  if (key.length < 32 || !/^[A-Za-z0-9_-]{15,}$/.test(folderId)) {
    throw new Error('Weekly backup is not configured. Set the two Script Properties.');
  }
  return { props: props, key: key, folderId: folderId };
}

function kofadHeader_(headers, name) {
  const key = Object.keys(headers).find(function (h) {
    return h.toLowerCase() === name.toLowerCase();
  });
  return key ? String(headers[key]) : '';
}

function kofadBackupFile_(file) {
  return file.getName().startsWith(KOFAD_BACKUP_PREFIX) &&
    file.getName().endsWith(KOFAD_BACKUP_SUFFIX);
}

function kofadAlreadySavedToday_(folder, dateString) {
  const files = folder.getFiles();
  while (files.hasNext()) {
    const file = files.next();
    if (kofadBackupFile_(file) &&
        file.getName().startsWith(KOFAD_BACKUP_PREFIX + dateString + '-') &&
        file.getDescription().indexOf('verified-sha256=') !== -1) {
      return true;
    }
  }
  return false;
}

function kofadPruneAfterVerifiedUpload_(folder) {
  const files = folder.getFiles();
  const backups = [];
  while (files.hasNext()) {
    const file = files.next();
    if (kofadBackupFile_(file) &&
        file.getDescription().indexOf('verified-sha256=') !== -1) {
      backups.push(file);
    }
  }
  backups.sort(function (a, b) {
    return b.getDateCreated().getTime() - a.getDateCreated().getTime();
  });
  // Retain four successfully uploaded and SHA-256 verified archives.
  // Never clean up backups unless a new copy was verified in THIS run.
  backups.slice(KOFAD_BACKUP_KEEP).forEach(function (file) {
    file.setTrashed(true);
  });
}

function weeklyKofadBackup() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(2000)) {
    throw new Error('Another KOFAD backup execution is already running.');
  }
  try {
    const config = kofadSettings_();
    const folder = DriveApp.getFolderById(config.folderId);
    const now = new Date();
    const dateOnly = Utilities.formatDate(now, KOFAD_BACKUP_TZ, 'yyyy-MM-dd');
    if (kofadAlreadySavedToday_(folder, dateOnly)) {
      console.log('A verified KOFAD backup is already present today. No new backup requested.');
      return;
    }

    const timestamp = String(Math.floor(Date.now() / 1000));
    const toSign = 'v1\nPOST\n/settings/backup/google/export/\n' + timestamp;
    const signature = kofadHex_(Utilities.computeHmacSha256Signature(toSign, config.key));
    const response = UrlFetchApp.fetch(KOFAD_BACKUP_URL, {
      method: 'post',
      contentType: 'application/octet-stream',
      payload: '',
      headers: {
        'X-Kofad-Backup-Timestamp': timestamp,
        'X-Kofad-Backup-Signature': signature
      },
      muteHttpExceptions: true,
      followRedirects: false,
      validateHttpsCertificates: true
    });
    if (response.getResponseCode() !== 200) {
      throw new Error('KOFAD refused backup export (HTTP ' + response.getResponseCode() +
        '). Check activation, API signing, export size and service health.');
    }
    const headerHash = kofadHeader_(response.getAllHeaders(), 'X-Kofad-Backup-SHA256');
    if (!/^[a-f0-9]{64}$/.test(headerHash)) {
      throw new Error('KOFAD did not provide a valid backup SHA-256 header.');
    }
    const bytes = response.getContent();
    if (bytes.length < 256 || bytes.length > KOFAD_BACKUP_MAX_BYTES) {
      throw new Error('KOFAD backup is empty or exceeds Apps Script safe transfer size.');
    }
    const magic = bytes.slice(0, KOFAD_ENCRYPTED_MAGIC.length).map(function (b) {
      return String.fromCharCode(b & 255);
    }).join('');
    if (magic !== KOFAD_ENCRYPTED_MAGIC) {
      throw new Error('Server response is not a KOFAD encrypted backup archive.');
    }
    const digest = kofadHex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, bytes));
    if (digest !== headerHash) {
      throw new Error('Downloaded backup SHA-256 does not match server digest.');
    }

    const dateStamp = Utilities.formatDate(now, KOFAD_BACKUP_TZ, 'yyyy-MM-dd-HHmm');
    const filename = KOFAD_BACKUP_PREFIX + dateStamp + KOFAD_BACKUP_SUFFIX;
    const saved = folder.createFile(Utilities.newBlob(
      bytes, 'application/octet-stream', filename
    ));
    try {
      if (saved.getSize() !== bytes.length) {
        throw new Error('Google Drive reported an unexpected saved backup size.');
      }
      const verifyBytes = saved.getBlob().getBytes();
      const verifiedHash = kofadHex_(
        Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, verifyBytes)
      );
      if (verifiedHash !== headerHash) {
        throw new Error('Google Drive re-download checksum failed.');
      }
    } catch (error) {
      saved.setTrashed(true); // Only the new, invalid upload; never previous backups.
      throw error;
    }

    saved.setDescription('verified-sha256=' + headerHash +
      '; backup-date=' + dateStamp +
      '; size-bytes=' + bytes.length +
      '; status=encrypted-upload-integrity-verified; restore-rehearsal=pending');
    config.props.setProperty('KOFAD_LAST_BACKUP_SUCCESS_UTC', new Date().toISOString());
    config.props.setProperty('KOFAD_LAST_BACKUP_FILE_ID', saved.getId());
    kofadPruneAfterVerifiedUpload_(folder);
    try {
      MailApp.sendEmail(
        KOFAD_BACKUP_NOTIFY,
        'KOFAD weekly backup stored — ' + dateOnly,
        'The encrypted weekly backup was stored and its SHA-256 checksum verified.\n\n' +
        'File: ' + filename + '\nBytes: ' + bytes.length +
        '\nSHA-256: ' + headerHash +
        '\nGoogle Drive: ' + saved.getUrl() +
        '\n\nThis is an integrity check, not a tested restore. Keep the offline ' +
        'backup passphrase and schedule regular isolated recovery rehearsals.'
      );
    } catch (mailError) {
      console.error('Backup verified, but success notification could not be sent.');
    }
  } catch (error) {
    try {
      MailApp.sendEmail(
        KOFAD_BACKUP_NOTIFY,
        'ACTION REQUIRED: KOFAD weekly backup failed',
        'A new KOFAD backup has NOT been confirmed. Previous archives were preserved.\n' +
        'Please inspect the Google Apps Script execution log and KOFAD website health.\n' +
        'Failure: ' + String(error.message || error).slice(0, 450)
      );
    } catch (notifyError) {
      console.error('Backup failed and email alert could not be sent.');
    }
    throw error;
  } finally {
    lock.releaseLock();
  }
}

function installWeeklyKofadTrigger() {
  kofadSettings_(); // Fail before creating an unusable trigger.
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === 'weeklyKofadBackup') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
  // Runs on Sunday, within the 2–3 AM Ghana-time window.
  ScriptApp.newTrigger('weeklyKofadBackup')
    .timeBased()
    .everyWeeks(1)
    .onWeekDay(ScriptApp.WeekDay.SUNDAY)
    .atHour(2)
    .inTimezone(KOFAD_BACKUP_TZ)
    .create();
  console.log('One weekly backup trigger installed for Sundays, 2–3 AM Ghana.');
}
