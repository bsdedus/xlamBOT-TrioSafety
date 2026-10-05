; Установщик xlamBOT.
;
; Собирается из уже готовой папки dist\xlamBOT, поэтому порядок такой:
;   1. PyInstaller собирает программу      -> dist\xlamBOT
;   2. этот скрипт собирает установщик     -> dist\xlamBOT-Setup-x.y.z.exe
; Скрипт install.ps1 делает обе части сам.

#define AppName "xlamBOT Trio Safety"
; Версия обязана совпадать с XLAMBOT_VERSION в utils.py, иначе exe и
; установщик будут называть сборку разными числами.
#include "build_assets\version.iss"
#define AppPublisher "xlamBOT"
#define AppExeName "xlamBOT.exe"

[Setup]
; Local patch uses a distinct identity so its validation cannot replace v0.8.15.
AppId={{CE3B1D7A-9B18-42B7-A63F-0BC5247C0815}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#NumericVersion}
VersionInfoTextVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=xlamBOT-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Без прав администратора: ставим в профиль пользователя.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
; Подсказка на случай, если у человека нет эмулятора
ChangesAssociations=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительно:"
Name: "quicklaunchicon"; Description: "Создать значок в области быстрого запуска"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
; Собранная программа целиком: exe, _internal, модели, переносимый Tesseract.
Source: "dist\xlamBOT\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// После установки говорим, что делать дальше: без эмулятора и включённой
// отладки по ADB программа работать не с чем.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    if (not WizardSilent) and DirExists(ExpandConstant('{app}\_internal\vendor\tesseract')) then
      MsgBox('Готово.' + #13#10 +
             'При первом запуске мастер сам найдёт устройство.' + #13#10 +
             'Если устройство не найдено - проверьте, что в эмуляторе включена отладка по ADB.',
             mbInformation, MB_OK);
end;
