/**
 * Patches the Tauri-generated installer.nsi to apply Recall's dark theme,
 * re-compiles with makensis, and replaces the bundle .exe.
 *
 * Run AFTER: pnpm tauri build --bundles nsis
 */
import { readFileSync, writeFileSync, readdirSync, copyFileSync } from 'fs';
import { execSync } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '..');
const NSIS_DIR = path.join(root, 'src-tauri', 'target', 'release', 'nsis', 'x64');
const NSI = path.join(NSIS_DIR, 'installer.nsi');
const BUNDLE_DIR = path.join(root, 'src-tauri', 'target', 'release', 'bundle', 'nsis');
const MAKENSIS = path.join(process.env.LOCALAPPDATA, 'tauri', 'NSIS', 'makensis.exe');

let nsi = readFileSync(NSI, 'utf8').replace(/\r\n/g, '\n');

function patch(from, to, label) {
  if (!nsi.includes(from)) throw new Error(`Patch failed — pattern not found: ${label || from.slice(0, 60)}`);
  nsi = nsi.replace(from, to);
  console.log(`  ✓ ${label || 'patch applied'}`);
}

console.log('Patching installer.nsi with dark theme...');

// 1. Color defines — inject after STARTMENUFOLDER define
patch(
  '!define STARTMENUFOLDER ""',
  '!define STARTMENUFOLDER ""\n\n; == Recall Dark Theme ==\n' +
  '!define THEME_BG     "08080f"\n' +
  '!define THEME_FG     "d8d8f0"\n' +
  '!define THEME_ACCENT "00e5ff"',
  'color defines'
);

// 2. Dark theme macro — inject after ${StrLoc} instantiation
patch(
  '${StrLoc}\n',
  '${StrLoc}\n\n' +
  '!macro _DarkThisPage\n' +
  '  Push $R0\n' +
  '  Push $R1\n' +
  '  FindWindow $R0 "#32770" "" $HWNDPARENT\n' +
  '  ${If} $R0 <> 0\n' +
  '    SetCtlColors $R0 "${THEME_FG}" "${THEME_BG}"\n' +
  '    System::Call \'user32::GetWindow(p$R0, i5)p.r1\'\n' +
  '    ${While} $R1 <> 0\n' +
  '      SetCtlColors $R1 "${THEME_FG}" "${THEME_BG}"\n' +
  '      System::Call \'user32::GetWindow(p$R1, i2)p.r1\'\n' +
  '    ${EndWhile}\n' +
  '  ${EndIf}\n' +
  '  Pop $R1\n' +
  '  Pop $R0\n' +
  '!macroend\n' +
  '!define DarkThisPage "!insertmacro _DarkThisPage"\n',
  '_DarkThisPage macro'
);

// 3. Welcome page SHOW callback
patch(
  '!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive\n!insertmacro MUI_PAGE_WELCOME',
  '!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive\n' +
  '!define MUI_PAGE_CUSTOMFUNCTION_SHOW DarkWelcomeShow\n' +
  '!insertmacro MUI_PAGE_WELCOME',
  'Welcome SHOW callback'
);

// 4. Directory page SHOW callback (unique: preceded by "5. Choose install directory page" comment)
patch(
  '; 5. Choose install directory page\n!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive\n!insertmacro MUI_PAGE_DIRECTORY',
  '; 5. Choose install directory page\n' +
  '!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive\n' +
  '!define MUI_PAGE_CUSTOMFUNCTION_SHOW DarkDirectoryShow\n' +
  '!insertmacro MUI_PAGE_DIRECTORY',
  'Directory SHOW callback'
);

// 5. StartMenu page SHOW callback
patch(
  '!insertmacro MUI_PAGE_STARTMENU Application $AppStartMenuFolder',
  '!define MUI_PAGE_CUSTOMFUNCTION_SHOW DarkStartMenuShow\n' +
  '!insertmacro MUI_PAGE_STARTMENU Application $AppStartMenuFolder',
  'StartMenu SHOW callback'
);

// 6. InstFiles page SHOW callback
patch(
  '; 7. Installation page\n!insertmacro MUI_PAGE_INSTFILES',
  '; 7. Installation page\n' +
  '!define MUI_PAGE_CUSTOMFUNCTION_SHOW DarkInstFilesShow\n' +
  '!insertmacro MUI_PAGE_INSTFILES',
  'InstFiles SHOW callback'
);

// 7. Finish page SHOW callback (unique: preceded by MUI_FINISHPAGE_RUN_FUNCTION line)
patch(
  '!define MUI_FINISHPAGE_RUN_FUNCTION RunMainBinary\n!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive\n!insertmacro MUI_PAGE_FINISH',
  '!define MUI_FINISHPAGE_RUN_FUNCTION RunMainBinary\n' +
  '!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive\n' +
  '!define MUI_PAGE_CUSTOMFUNCTION_SHOW DarkFinishShow\n' +
  '!insertmacro MUI_PAGE_FINISH',
  'Finish SHOW callback'
);

// 8. Uninstall Confirm: add dark theme at start of existing un.ConfirmShow function
patch(
  'Function un.ConfirmShow ; Add add a `Delete app data` check box\n' +
  '  ; $1 inner dialog HWND',
  'Function un.ConfirmShow ; Add add a `Delete app data` check box\n' +
  '  ${DarkThisPage}\n' +
  '  ; $1 inner dialog HWND',
  'un.ConfirmShow dark theme'
);

// 9. Uninstall InstFiles SHOW callback
patch(
  '; 2. Uninstalling Page\n!insertmacro MUI_UNPAGE_INSTFILES',
  '; 2. Uninstalling Page\n' +
  '!define MUI_PAGE_CUSTOMFUNCTION_SHOW un.DarkUnInstFilesShow\n' +
  '!insertmacro MUI_UNPAGE_INSTFILES',
  'Uninstall InstFiles SHOW callback'
);

// 10. PageReinstall: add dark theme after nsDialogs::Create
patch(
  '    nsDialogs::Create 1018\n' +
  '    Pop $R4\n' +
  '    ${IfThen} $(^RTL) = 1 ${|} nsDialogs::SetRTL $(^RTL) ${|}',
  '    nsDialogs::Create 1018\n' +
  '    Pop $R4\n' +
  '    ${IfThen} $(^RTL) = 1 ${|} nsDialogs::SetRTL $(^RTL) ${|}\n' +
  '    SetCtlColors $R4 "${THEME_FG}" "${THEME_BG}"',
  'PageReinstall dark bg'
);

// 11. Add all Show callback functions before RestorePreviousInstallLocation
const DARK_FUNCTIONS =
  'Function DarkWelcomeShow\n  ${DarkThisPage}\nFunctionEnd\n' +
  'Function DarkDirectoryShow\n  ${DarkThisPage}\nFunctionEnd\n' +
  'Function DarkStartMenuShow\n  ${DarkThisPage}\nFunctionEnd\n' +
  'Function DarkInstFilesShow\n  ${DarkThisPage}\nFunctionEnd\n' +
  'Function DarkFinishShow\n  ${DarkThisPage}\nFunctionEnd\n' +
  'Function un.DarkUnInstFilesShow\n  ${DarkThisPage}\nFunctionEnd\n';

patch(
  'Function RestorePreviousInstallLocation',
  DARK_FUNCTIONS + '\nFunction RestorePreviousInstallLocation',
  'Dark callback functions'
);

writeFileSync(NSI, nsi, 'utf8');
console.log('✓ Patched installer.nsi written');

// Re-compile
console.log('\nCompiling dark-themed installer...');
execSync(`"${MAKENSIS}" installer.nsi`, { cwd: NSIS_DIR, stdio: 'inherit' });

// Replace bundle .exe
const files = readdirSync(BUNDLE_DIR);
const exe = files.find(f => f.endsWith('-setup.exe'));
if (!exe) throw new Error(`No setup .exe found in ${BUNDLE_DIR}`);

const src = path.join(NSIS_DIR, 'nsis-output.exe');
const dst = path.join(BUNDLE_DIR, exe);
copyFileSync(src, dst);
console.log(`\n✓ Replaced ${exe} with dark-themed installer`);
