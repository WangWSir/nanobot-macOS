const { notarize } = require('@electron/notarize');
const { execSync } = require('child_process');
const path = require('path');

exports.default = async function notarizing(context) {
  const { electronPlatformName, appOutDir } = context;
  if (electronPlatformName !== 'darwin') return;

  const identity = process.env.CSC_NAME || 'Developer ID Application: Your Name (TEAMID)';
  const signing = Boolean(
    process.env.APPLE_ID &&
    process.env.APPLE_APP_SPECIFIC_PASSWORD &&
    process.env.APPLE_TEAM_ID
  );
  if (!signing) {
    console.log('[notarize] APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID not set; skipping signing and notarization.');
    return;
  }

  const appName = context.packager.appInfo.productFilename;
  const appPath = path.join(appOutDir, `${appName}.app`);
  const serverPath = path.join(appPath, 'Contents', 'Resources', 'nanobot-server');

  // 深度签名 nanobot-server 内部所有二进制
  execSync(
    `codesign --deep --force --options runtime --timestamp \
     --entitlements build/entitlements.mac.plist \
     --sign "${identity}" \
     "${serverPath}"`,
    { stdio: 'inherit' }
  );

  // 对最终 .app 签名
  execSync(
    `codesign --deep --force --options runtime --timestamp \
     --entitlements build/entitlements.mac.plist \
     --sign "${identity}" \
     "${appPath}"`,
    { stdio: 'inherit' }
  );

  // 提交公证（需设置环境变量）
  await notarize({
    appPath,
    appleId: process.env.APPLE_ID,
    appleIdPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD,
    teamId: process.env.APPLE_TEAM_ID,
  });
};
