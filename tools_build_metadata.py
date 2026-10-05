"""Generate installer/PE versions from the single runtime version source."""
from pathlib import Path
from version import __version__


def main():
    folder = Path(__file__).resolve().parent / 'build_assets'
    folder.mkdir(exist_ok=True)
    base, _, post = __version__.partition('.post')
    number = tuple(map(int, base.split('.'))) + (int(post or 0),)
    numeric = '.'.join(map(str, number))
    folder.joinpath('version.iss').write_text(f'#define AppVersion "{__version__}"\n#define NumericVersion "{numeric}"\n', encoding='utf-8')
    folder.joinpath('version_info.txt').write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={number}, prodvers={number}, "
        "mask=0x3f, flags=0, OS=0x40004, fileType=0x1, subtype=0, date=(0,0)), "
        "kids=[StringFileInfo([StringTable('040904B0', ["
        f"StringStruct('FileVersion','{__version__}'), StringStruct('ProductVersion','{__version__}'), "
        "StringStruct('ProductName','xlamBOT'), StringStruct('OriginalFilename','xlamBOT.exe')])]), "
        "VarFileInfo([VarStruct('Translation',[1033,1200])])])", encoding='utf-8')


if __name__ == '__main__':
    main()
