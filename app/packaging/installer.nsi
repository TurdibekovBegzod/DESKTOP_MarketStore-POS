; -------------------------------------------------------------------------
; NSIS Modern UI 2 Script for MarketStore POS
; -------------------------------------------------------------------------

!include "MUI2.nsh"
!include "FileFunc.nsh"

; General Definitions
!define PRODUCT_NAME "MarketStore POS"
!define PRODUCT_VERSION "1.0.4"
!define PRODUCT_PUBLISHER "MarketStore Team"
!define PRODUCT_WEB_SITE "https://marketstore.uz"
!define PRODUCT_EXE "MarketStore-POS.exe"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"
!define PRODUCT_UNINST_ROOT_KEY "HKLM"

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "..\dist\MarketStore_Setup_${PRODUCT_VERSION}.exe"
InstallDir "$PROGRAMFILES64\MarketStore POS"
InstallDirRegKey HKLM "Software\MarketStore POS" "InstallDir"
RequestExecutionLevel admin

; Compression
SetCompressor /SOLID lzma

; Interface Configuration
!define MUI_ABORTWARNING
!define MUI_ICON "..\app\images\desktop_icon.ico"
!define MUI_UNICON "..\app\images\desktop_icon.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP_NOSTRETCH

; Installer Pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES

; Finish Page: Run app checkbox
!define MUI_FINISHPAGE_RUN "$INSTDIR\${PRODUCT_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "MarketStore POS dasturini ishga tushirish"
!insertmacro MUI_PAGE_FINISH

; Uninstaller Pages
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; Language
!insertmacro MUI_LANGUAGE "English"

; -------------------------------------------------------------------------
; Installation Section
; -------------------------------------------------------------------------
Section "MainSection" SEC01
    SetOutPath "$INSTDIR"
    SetOverwrite on

    ; Close running instance if any
    DetailPrint "Eski dastur nusxasi tekshirilmoqda..."
    nsExec::Exec 'taskkill /F /IM "${PRODUCT_EXE}" /T'

    ; Copy all built files from dist folder
    File /r "..\dist\MarketStore-POS\*.*"

    ; Create Shortcuts
    CreateDirectory "$SMPROGRAMS\MarketStore POS"
    CreateShortcut "$SMPROGRAMS\MarketStore POS\MarketStore POS.lnk" "$INSTDIR\${PRODUCT_EXE}" "" "$INSTDIR\${PRODUCT_EXE}" 0
    CreateShortcut "$SMPROGRAMS\MarketStore POS\O'chirish (Uninstall).lnk" "$INSTDIR\Uninstall.exe"
    CreateShortcut "$DESKTOP\MarketStore POS.lnk" "$INSTDIR\${PRODUCT_EXE}" "" "$INSTDIR\${PRODUCT_EXE}" 0

    ; Write Uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Write Registry Keys for Windows Add/Remove Programs
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "DisplayIcon" "$INSTDIR\${PRODUCT_EXE}"
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "URLInfoAbout" "${PRODUCT_WEB_SITE}"
    WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"

    ; Estimated Size
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "EstimatedSize" "$0"
SectionEnd

; -------------------------------------------------------------------------
; Uninstallation Section
; -------------------------------------------------------------------------
Section Uninstall
    ; Terminate running app
    nsExec::Exec 'taskkill /F /IM "${PRODUCT_EXE}" /T'

    ; Remove Shortcuts
    Delete "$DESKTOP\MarketStore POS.lnk"
    Delete "$SMPROGRAMS\MarketStore POS\MarketStore POS.lnk"
    Delete "$SMPROGRAMS\MarketStore POS\O'chirish (Uninstall).lnk"
    RMDir "$SMPROGRAMS\MarketStore POS"

    ; Remove Installed Files
    RMDir /r "$INSTDIR"

    ; Remove Registry Keys
    DeleteRegKey ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}"
    DeleteRegKey HKLM "Software\MarketStore POS"
SectionEnd
