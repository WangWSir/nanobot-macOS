const fs = require('fs');
const path = require('path');

// 构建完成后清理 dist/，只保留 .dmg
const distDir = path.join(__dirname, '..', 'dist');
if (!fs.existsSync(distDir)) process.exit(0);

const keep = new Set(['.npmignore']);
for (const entry of fs.readdirSync(distDir)) {
  if (entry.endsWith('.dmg')) continue;
  fs.rmSync(path.join(distDir, entry), { recursive: true, force: true });
}
console.log('[clean-dist] kept .dmg artifacts, removed intermediate outputs');
