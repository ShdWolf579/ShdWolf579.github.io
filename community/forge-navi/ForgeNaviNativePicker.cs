using System;
using System.IO;
using System.Runtime.InteropServices;

namespace ForgeNaviNativePicker
{
    internal static class Program
    {
        private const uint SELECT_FOLDER_BUTTON = 0x4E415649; // 'NAVI'
        private const uint FOS_FORCEFILESYSTEM = 0x00000040;
        private const uint FOS_PATHMUSTEXIST = 0x00000800;
        private const uint FOS_FILEMUSTEXIST = 0x00001000;
        private const uint FOS_NOCHANGEDIR = 0x00000008;
        private const int ERROR_CANCELLED_HRESULT = unchecked((int)0x800704C7);

        [STAThread]
        private static int Main(string[] args)
        {
            if (args.Length > 0 && args[0] == "--self-test")
            {
                Console.WriteLine("OK");
                return 0;
            }

            IntPtr owner = IntPtr.Zero;
            if (args.Length > 0)
            {
                long rawOwner;
                if (long.TryParse(args[0], out rawOwner))
                    owner = new IntPtr(rawOwner);
            }

            string initialFolder = args.Length > 1 ? args[1] : null;

            Type dialogType = Type.GetTypeFromCLSID(new Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7"));
            object dialogObject = Activator.CreateInstance(dialogType);
            IFileDialog dialog = (IFileDialog)dialogObject;
            IFileDialogCustomize customize = (IFileDialogCustomize)dialogObject;
            DialogEventSink sink = null;
            uint cookie = 0;

            try
            {
                dialog.SetTitle("Open Forge Project");
                dialog.SetOkButtonLabel("Open");

                COMDLG_FILTERSPEC[] filters = new COMDLG_FILTERSPEC[]
                {
                    new COMDLG_FILTERSPEC("ZIP archives (*.zip)", "*.zip")
                };
                dialog.SetFileTypes((uint)filters.Length, filters);

                uint options;
                dialog.GetOptions(out options);
                options |= FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_FILEMUSTEXIST | FOS_NOCHANGEDIR;
                dialog.SetOptions(options);

                if (!String.IsNullOrWhiteSpace(initialFolder) && Directory.Exists(initialFolder))
                {
                    IShellItem initialItem = null;
                    try
                    {
                        Guid iidShellItem = typeof(IShellItem).GUID;
                        SHCreateItemFromParsingName(initialFolder, IntPtr.Zero, ref iidShellItem, out initialItem);
                        if (initialItem != null)
                            dialog.SetFolder(initialItem);
                    }
                    catch { }
                    finally
                    {
                        if (initialItem != null)
                            Marshal.ReleaseComObject(initialItem);
                    }
                }

                customize.AddPushButton(SELECT_FOLDER_BUTTON, "Select This Folder");

                sink = new DialogEventSink(dialog, SELECT_FOLDER_BUTTON);
                int hr = dialog.Advise(sink, out cookie);
                if (hr < 0)
                    Marshal.ThrowExceptionForHR(hr);

                hr = dialog.Show(owner);
                if (hr == ERROR_CANCELLED_HRESULT)
                    return 1;
                if (hr < 0)
                    Marshal.ThrowExceptionForHR(hr);

                if (!String.IsNullOrEmpty(sink.SelectedFolder))
                {
                    Console.WriteLine(sink.SelectedFolder);
                    return 0;
                }

                IShellItem result = null;
                try
                {
                    dialog.GetResult(out result);
                    string path = ShellItemPath(result);
                    if (String.IsNullOrEmpty(path))
                        return 2;
                    Console.WriteLine(path);
                    return 0;
                }
                finally
                {
                    if (result != null)
                        Marshal.ReleaseComObject(result);
                }
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(ex.Message);
                return 2;
            }
            finally
            {
                try
                {
                    if (cookie != 0)
                        dialog.Unadvise(cookie);
                }
                catch { }

                if (customize != null)
                    Marshal.ReleaseComObject(customize);
                if (dialog != null)
                    Marshal.ReleaseComObject(dialog);
            }
        }

        private static string ShellItemPath(IShellItem item)
        {
            if (item == null)
                return null;

            IntPtr ptr;
            item.GetDisplayName(SIGDN.FILESYSPATH, out ptr);
            if (ptr == IntPtr.Zero)
                return null;
            try
            {
                return Marshal.PtrToStringUni(ptr);
            }
            finally
            {
                Marshal.FreeCoTaskMem(ptr);
            }
        }

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
        private static extern void SHCreateItemFromParsingName(
            string pszPath,
            IntPtr pbc,
            ref Guid riid,
            [MarshalAs(UnmanagedType.Interface)] out IShellItem ppv);

        [ComVisible(true)]
        private sealed class DialogEventSink : IFileDialogEvents, IFileDialogControlEvents
        {
            private readonly IFileDialog _dialog;
            private readonly uint _folderButton;
            public string SelectedFolder { get; private set; }

            public DialogEventSink(IFileDialog dialog, uint folderButton)
            {
                _dialog = dialog;
                _folderButton = folderButton;
            }

            public int OnFileOk(IFileDialog pfd) { return 0; }
            public int OnFolderChanging(IFileDialog pfd, IShellItem psiFolder) { return 0; }
            public int OnFolderChange(IFileDialog pfd) { return 0; }
            public int OnSelectionChange(IFileDialog pfd) { return 0; }
            public int OnShareViolation(IFileDialog pfd, IShellItem psi, out FDE_SHAREVIOLATION_RESPONSE response)
            {
                response = FDE_SHAREVIOLATION_RESPONSE.DEFAULT;
                return 0;
            }
            public int OnTypeChange(IFileDialog pfd) { return 0; }
            public int OnOverwrite(IFileDialog pfd, IShellItem psi, out FDE_OVERWRITE_RESPONSE response)
            {
                response = FDE_OVERWRITE_RESPONSE.DEFAULT;
                return 0;
            }

            public int OnItemSelected(IFileDialogCustomize pfdc, uint dwIDCtl, uint dwIDItem) { return 0; }

            public int OnButtonClicked(IFileDialogCustomize pfdc, uint dwIDCtl)
            {
                if (dwIDCtl != _folderButton)
                    return 0;

                IShellItem folder = null;
                try
                {
                    _dialog.GetFolder(out folder);
                    SelectedFolder = ShellItemPath(folder);
                    if (!String.IsNullOrEmpty(SelectedFolder))
                        _dialog.Close(0);
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine(ex.Message);
                }
                finally
                {
                    if (folder != null)
                        Marshal.ReleaseComObject(folder);
                }
                return 0;
            }

            public int OnCheckButtonToggled(IFileDialogCustomize pfdc, uint dwIDCtl, bool bChecked) { return 0; }
            public int OnControlActivating(IFileDialogCustomize pfdc, uint dwIDCtl) { return 0; }
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct COMDLG_FILTERSPEC
        {
            [MarshalAs(UnmanagedType.LPWStr)]
            public string pszName;
            [MarshalAs(UnmanagedType.LPWStr)]
            public string pszSpec;

            public COMDLG_FILTERSPEC(string name, string spec)
            {
                pszName = name;
                pszSpec = spec;
            }
        }

        private enum SIGDN : uint
        {
            FILESYSPATH = 0x80058000
        }

        private enum FDAP
        {
            BOTTOM = 0,
            TOP = 1
        }

        private enum FDE_SHAREVIOLATION_RESPONSE
        {
            DEFAULT = 0,
            ACCEPT = 1,
            REFUSE = 2
        }

        private enum FDE_OVERWRITE_RESPONSE
        {
            DEFAULT = 0,
            ACCEPT = 1,
            REFUSE = 2
        }

        [ComImport]
        [Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IShellItem
        {
            void BindToHandler(IntPtr pbc, [MarshalAs(UnmanagedType.LPStruct)] Guid bhid, [MarshalAs(UnmanagedType.LPStruct)] Guid riid, out IntPtr ppv);
            void GetParent(out IShellItem ppsi);
            void GetDisplayName(SIGDN sigdnName, out IntPtr ppszName);
            void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
            void Compare(IShellItem psi, uint hint, out int piOrder);
        }

        [ComImport]
        [Guid("42F85136-DB7E-439C-85F1-E4075D135FC8")]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IFileDialog
        {
            [PreserveSig]
            int Show(IntPtr parent);

            void SetFileTypes(uint cFileTypes, [MarshalAs(UnmanagedType.LPArray)] COMDLG_FILTERSPEC[] rgFilterSpec);
            void SetFileTypeIndex(uint iFileType);
            void GetFileTypeIndex(out uint piFileType);

            [PreserveSig]
            int Advise([MarshalAs(UnmanagedType.Interface)] IFileDialogEvents pfde, out uint pdwCookie);

            void Unadvise(uint dwCookie);
            void SetOptions(uint fos);
            void GetOptions(out uint pfos);
            void SetDefaultFolder(IShellItem psi);
            void SetFolder(IShellItem psi);
            void GetFolder(out IShellItem ppsi);
            void GetCurrentSelection(out IShellItem ppsi);
            void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
            void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
            void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
            void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
            void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
            void GetResult(out IShellItem ppsi);
            void AddPlace(IShellItem psi, FDAP fdap);
            void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);

            [PreserveSig]
            int Close(int hr);

            void SetClientGuid(ref Guid guid);
            void ClearClientData();
            void SetFilter(IntPtr pFilter);
        }

        [ComImport]
        [Guid("E6FDD21A-163F-4975-9C8C-A69F1BA37034")]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IFileDialogCustomize
        {
            void EnableOpenDropDown(uint dwIDCtl);
            void AddMenu(uint dwIDCtl, [MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
            void AddPushButton(uint dwIDCtl, [MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
            void AddComboBox(uint dwIDCtl);
            void AddRadioButtonList(uint dwIDCtl);
            void AddCheckButton(uint dwIDCtl, [MarshalAs(UnmanagedType.LPWStr)] string pszLabel, bool bChecked);
            void AddEditBox(uint dwIDCtl, [MarshalAs(UnmanagedType.LPWStr)] string pszText);
            void AddSeparator(uint dwIDCtl);
            void AddText(uint dwIDCtl, [MarshalAs(UnmanagedType.LPWStr)] string pszText);
            void SetControlLabel(uint dwIDCtl, [MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
            void GetControlState(uint dwIDCtl, out uint pdwState);
            void SetControlState(uint dwIDCtl, uint dwState);
            void GetEditBoxText(uint dwIDCtl, [MarshalAs(UnmanagedType.LPWStr)] out string ppszText);
            void SetEditBoxText(uint dwIDCtl, [MarshalAs(UnmanagedType.LPWStr)] string pszText);
            void GetCheckButtonState(uint dwIDCtl, out bool pbChecked);
            void SetCheckButtonState(uint dwIDCtl, bool bChecked);
            void AddControlItem(uint dwIDCtl, uint dwIDItem, [MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
            void RemoveControlItem(uint dwIDCtl, uint dwIDItem);
            void RemoveAllControlItems(uint dwIDCtl);
            void GetControlItemState(uint dwIDCtl, uint dwIDItem, out uint pdwState);
            void SetControlItemState(uint dwIDCtl, uint dwIDItem, uint dwState);
            void GetSelectedControlItem(uint dwIDCtl, out uint pdwIDItem);
            void SetSelectedControlItem(uint dwIDCtl, uint dwIDItem);
            void StartVisualGroup(uint dwIDCtl, [MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
            void EndVisualGroup();
            void MakeProminent(uint dwIDCtl);
            void SetControlItemText(uint dwIDCtl, uint dwIDItem, [MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
        }

        [ComImport]
        [Guid("973510DB-7D7F-452B-8975-74A85828D354")]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IFileDialogEvents
        {
            [PreserveSig]
            int OnFileOk(IFileDialog pfd);

            [PreserveSig]
            int OnFolderChanging(IFileDialog pfd, IShellItem psiFolder);

            [PreserveSig]
            int OnFolderChange(IFileDialog pfd);

            [PreserveSig]
            int OnSelectionChange(IFileDialog pfd);

            [PreserveSig]
            int OnShareViolation(IFileDialog pfd, IShellItem psi, out FDE_SHAREVIOLATION_RESPONSE pResponse);

            [PreserveSig]
            int OnTypeChange(IFileDialog pfd);

            [PreserveSig]
            int OnOverwrite(IFileDialog pfd, IShellItem psi, out FDE_OVERWRITE_RESPONSE pResponse);
        }

        [ComImport]
        [Guid("36116642-D713-4B97-9B83-7484A9D00433")]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IFileDialogControlEvents
        {
            [PreserveSig]
            int OnItemSelected(IFileDialogCustomize pfdc, uint dwIDCtl, uint dwIDItem);

            [PreserveSig]
            int OnButtonClicked(IFileDialogCustomize pfdc, uint dwIDCtl);

            [PreserveSig]
            int OnCheckButtonToggled(IFileDialogCustomize pfdc, uint dwIDCtl, bool bChecked);

            [PreserveSig]
            int OnControlActivating(IFileDialogCustomize pfdc, uint dwIDCtl);
        }
    }
}
