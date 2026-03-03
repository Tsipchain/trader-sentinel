#!/usr/bin/env node
/**
 * Patches expo-modules-core PermissionsService.kt to fix Kotlin 1.9.x null safety error.
 *
 * Error: Only safe (?.) or non-null asserted (!!.) calls are allowed on a nullable
 * receiver of type Array<(out) String!>?>
 *
 * Root cause: PackageInfo.requestedPermissions is @Nullable in the Android SDK,
 * and Kotlin 1.9.x enforces null checks on it more strictly than 1.8.x.
 */

const fs = require('fs');
const path = require('path');

const targetFile = path.resolve(
  __dirname,
  '../node_modules/expo-modules-core/android/src/main/java/expo/modules/adapters/react/permissions/PermissionsService.kt'
);

if (!fs.existsSync(targetFile)) {
  console.log('[patch-expo-modules] File not found, skipping:', targetFile);
  process.exit(0);
}

const original = '        return requestedPermissions.contains(permission)';
const patched  = '        return requestedPermissions?.contains(permission) ?: false';

let content = fs.readFileSync(targetFile, 'utf8');

if (content.includes(patched)) {
  console.log('[patch-expo-modules] Already patched, nothing to do.');
  process.exit(0);
}

if (!content.includes(original)) {
  console.warn('[patch-expo-modules] Target line not found – the upstream code may have changed. Skipping.');
  process.exit(0);
}

content = content.replace(original, patched);
fs.writeFileSync(targetFile, content, 'utf8');
console.log('[patch-expo-modules] Patched PermissionsService.kt successfully.');
