# -*- coding: utf-8 -*
__author__ = 'Trafimchuk Aliaksandr'

from collections import defaultdict
import idaapi
from idautils import FuncItems, CodeRefsTo
from idaapi import o_reg, o_imm, o_far, o_near, o_mem
import os
import traceback


HAS_PYSIDE = False
try:
    from PySide import QtGui, QtCore
    from PySide.QtGui import QTreeView, QVBoxLayout, QLineEdit

    HAS_PYSIDE = True
except ImportError:
    from PyQt5 import QtGui, QtCore
    from PyQt5.QtWidgets import QTreeView, QVBoxLayout, QLineEdit


# enable to allow PyCharm remote debug
RDEBUG = False
# adjust this value to be a full path to a debug egg
RDEBUG_EGG = r'c:\Program Files (x86)\JetBrains\PyCharm 2016.3\debug-eggs\pycharm-debug.egg'
RDEBUG_HOST = 'localhost'
RDEBUG_PORT = 12321


TAGS_IGNORE_LIST = {
    'OpenProcessToken',
    'DisconnectNamedPipe'
}

IGNORE_CALL_LIST = {
    'RtlNtStatusToDosError',
    'GetLastError',
    'SetLastError'
}

TAGS = {
    'net': ['WSAStartup', 'socket', 'recv', 'recvfrom', 'send', 'sendto', 'acccept', 'bind', 'listen', 'select',
            'setsockopt', 'ioctlsocket', 'closesocket', 'WSAAccept', 'WSARecv', 'WSARecvFrom', 'WSASend', 'WSASendTo',
            'WSASocket', 'WSAConnect', 'ConnectEx', 'TransmitFile', 'HTTPOpenRequest', 'HTTPSendRequest',
            'URLDownloadToFile', 'InternetCrackUrl', 'InternetOpen', 'InternetOpen', 'InternetConnect',
            'InternetOpenUrl', 'InternetQueryOption', 'InternetSetOption', 'InternetReadFile', 'InternetWriteFile',
            'InternetGetConnectedState', 'InternetSetStatusCallback', 'DnsQuery', 'getaddrinfo', 'GetAddrInfo',
            'GetAdaptersInfo', 'GetAdaptersAddresses', 'HttpQueryInfo', 'ObtainUserAgentString', 'WNetGetProviderName',
            'GetBestInterfaceEx', 'gethostbyname', 'getsockname', 'connect'],
    'spawn': ['CreateProcess', 'ShellExecute', 'ShellExecuteEx', 'system', 'CreateProcessInternal', 'NtCreateProcess',
              'ZwCreateProcess', 'NtCreateProcessEx', 'ZwCreateProcessEx', 'NtCreateUserProcess', 'ZwCreateUserProcess',
              'RtlCreateUserProcess', 'NtCreateSection', 'ZwCreateSection', 'NtOpenSection', 'ZwOpenSection',
              'NtAllocateVirtualMemory', 'ZwAllocateVirtualMemory', 'NtWriteVirtualMemory', 'ZwWriteVirtualMemory',
              'NtMapViewOfSection', 'ZwMapViewOfSection', 'OpenSCManager', 'CreateService', 'OpenService',
              'StartService', 'ControlService', 'ShellExecuteExA', 'ShellExecuteExW'],
    'inject': ['OpenProcess-disabled', 'ZwOpenProcess', 'NtOpenProcess', 'WriteProcessMemory', 'NtWriteVirtualMemory',
               'ZwWriteVirtualMemory', 'CreateRemoteThread', 'QueueUserAPC', 'ZwUnmapViewOfSection', 'NtUnmapViewOfSection'],
    'com': ['CoCreateInstance', 'CoInitializeSecurity', 'CoGetClassObject', 'OleConvertOLESTREAMToIStorage'],
    'crypto': ['CryptAcquireContext', 'CryptProtectData', 'CryptUnprotectData', 'CryptProtectMemory',
               'CryptUnprotectMemory', 'CryptDecrypt', 'CryptEncrypt', 'CryptHashData', 'CryptDecodeMessage',
               'CryptDecryptMessage', 'CryptEncryptMessage', 'CryptHashMessage', 'CryptExportKey', 'CryptGenKey',
               'CryptCreateHash', 'CryptDecodeObjectEx', 'EncryptMessage', 'DecryptMessage'],
    'kbd': ['SendInput', 'VkKeyScanA', 'VkKeyScanW']
}

blacklist = {'@__security_check_cookie@4', '__SEH_prolog4', '__SEH_epilog4'}
replacements = [
    ('??3@YAXPAX@Z', 'alloc'),
    ('?', '')
]


def get_addr_width():
    return '16' if idaapi.cvar.inf.is_64bit() else '8'


def decode_insn(ea):
    if idaapi.IDA_SDK_VERSION >= 700:
        insn = idaapi.insn_t()
        if idaapi.decode_insn(insn, ea) > 0:
            return insn
    else:
        if idaapi.decode_insn(ea):
            return idaapi.cmd.copy()


class AutoREView(idaapi.PluginForm):
    ADDR_ROLE = QtCore.Qt.UserRole + 1

    def __init__(self, data):
        super(AutoREView, self).__init__()
        self._data = data
        self.tv = None
        self._model = None

    def Show(self):
        return idaapi.PluginForm.Show(self, 'AutoRE', options=idaapi.PluginForm.FORM_PERSIST)

    def OnCreate(self, form):
        if HAS_PYSIDE:
            self.parent = self.FormToPySideWidget(form)
        else:
            self.parent = self.FormToPyQtWidget(form)

        self.tv = QTreeView()
        self.tv.setExpandsOnDoubleClick(False)

        root_layout = QVBoxLayout(self.parent)
        # self.le_filter = QLineEdit(self.parent)

        # root_layout.addWidget(self.le_filter)
        root_layout.addWidget(self.tv)

        self.parent.setLayout(root_layout)

        self._model = QtGui.QStandardItemModel()
        self._init_model()
        self.tv.setModel(self._model)

        self.tv.setColumnWidth(0, 200)
        self.tv.setColumnWidth(1, 300)
        self.tv.header().setStretchLastSection(True)

        self.tv.expandAll()

        self.tv.doubleClicked.connect(self.on_navigate_to_method_requested)
        # self.le_filter.textChanged.connect(self.on_filter_text_changed)

    def OnClose(self, form):
        # print 'TODO: OnClose(): clear the pointer to form in the plugin'
        pass

    def _tv_init_header(self, model):
        item_header = QtGui.QStandardItem("EA")
        item_header.setToolTip("Address")
        model.setHorizontalHeaderItem(0, item_header)

        item_header = QtGui.QStandardItem("Function name")
        model.setHorizontalHeaderItem(1, item_header)

        item_header = QtGui.QStandardItem("API called")
        model.setHorizontalHeaderItem(2, item_header)

    # noinspection PyMethodMayBeStatic
    def _tv_make_tag_item(self, name):
        rv = QtGui.QStandardItem(name)

        rv.setEditable(False)
        return [rv, QtGui.QStandardItem(), QtGui.QStandardItem()]

    def _tv_make_ref_item(self, tag, ref):
        ea_item = QtGui.QStandardItem(('%0' + get_addr_width() + 'X') % ref['ea'])
        ea_item.setEditable(False)
        ea_item.setData(ref['ea'], self.ADDR_ROLE)

        name_item = QtGui.QStandardItem(ref['name'])
        name_item.setEditable(False)
        name_item.setData(ref['ea'], self.ADDR_ROLE)

        apis = ', '.join(ref['tags'][tag])
        api_name = QtGui.QStandardItem(apis)
        api_name.setEditable(False)
        api_name.setData(ref['ea'], self.ADDR_ROLE)
        api_name.setToolTip(apis)

        return [ea_item, name_item, api_name]

    def _init_model(self):
        self._model.clear()

        root_node = self._model.invisibleRootItem()
        self._tv_init_header(self._model)

        for tag, refs in self._data.items():
            item_tag_list = self._tv_make_tag_item(tag)
            item_tag = item_tag_list[0]

            root_node.appendRow(item_tag_list)

            for ref in refs:
                ref_item_list = self._tv_make_ref_item(tag, ref)

                item_tag.appendRow(ref_item_list)

    def on_navigate_to_method_requested(self, index):
        addr = index.data(role=self.ADDR_ROLE)
        if addr is not None:
            idaapi.jumpto(addr)

    # def on_filter_text_changed(self, text):
    #     print 'on_text_changed: %s' % text


class auto_re_t(idaapi.plugin_t):
    flags = idaapi.PLUGIN_UNL
    comment = ""

    help = ""
    wanted_name = "Auto RE"
    wanted_hotkey = "Ctrl+Shift+M"

    _PREFIX_NAME = 'au_re_'
    _MIN_MAX_MATH_OPS_TO_ALLOW_RENAME = 10

    def __init__(self):
        super(auto_re_t, self).__init__()
        self._data = None

    def init(self):
        # self._cfg = None
        self.view = None
        # self._load_config()

        return idaapi.PLUGIN_OK

    # def _load_config(self):
    #     self._cfg = {'auto_rename': False}

    # def _store_config(self, cfg):
    #     pass

    def _handle_tags(self, fn, fn_an, known_refs):
        if known_refs:
            known_refs = dict(known_refs)
            for k, names in known_refs.items():
                existing = set(fn_an['tags'][k])
                new = set(names) - existing
                if new:
                    fn_an['tags'][k] += list(new)

        tags = dict(fn_an['tags'])
        if not tags:
            return
        print 'fn: %#08x tags: %s' % (fn.startEA, tags)
        cmt = idaapi.get_func_cmt(fn, True)
        if cmt:
            cmt += '\n'
        s = str(tags.keys())
        name = idaapi.get_ea_name(fn.startEA)
        item = {'ea': fn.startEA, 'name': name, 'tags': tags}
        if not cmt or s not in cmt:
            idaapi.set_func_cmt(fn, '%sTAGS: %s' % (cmt or '', s), True)
        # self.mark_position(fn.startEA, 'TAGS: %s' % s)
        for tag in tags:
            if tag not in self._data:
                self._data[tag] = list()
            self._data[tag].append(item)

    def _handle_calls(self, fn, fn_an):
        num_calls = len(fn_an['calls'])
        if num_calls != 1:
            return

        dis = fn_an['calls'][0]
        if dis.Op1.type not in (o_imm, o_far, o_near, o_mem):
            return

        ea = dis.Op1.value
        if not ea and dis.Op1.addr:
            ea = dis.Op1.addr

        if idaapi.has_dummy_name(idaapi.getFlags(ea)):
            return

        possible_name = idaapi.get_ea_name(ea)
        if not possible_name or possible_name in blacklist:
            return

        normalized = self.normalize_name(possible_name)

        # if self._cfg.get('auto_rename'):
        if len(fn_an['math']) < self._MIN_MAX_MATH_OPS_TO_ALLOW_RENAME:
            idaapi.do_name_anyway(fn.startEA, normalized)
        # TODO: add an API to the view
        print 'fn: %#08x: %d calls, %d math%s possible name: %s, normalized: %s' % (
            fn.startEA, len(fn_an['calls']), len(fn_an['math']), 'has bads' if fn_an['has_bads'] else '',
            possible_name, normalized)

    # noinspection PyMethodMayBeStatic
    def _check_is_jmp_wrapper(self, dis):
        # checks instructions like `jmp API`
        if dis.itype not in (idaapi.NN_jmp, idaapi.NN_jmpni, idaapi.NN_jmpfi, idaapi.NN_jmpshort):
            return

        # handle call wrappers like jmp GetProcAddress
        if dis.Op1.type == idaapi.o_mem and dis.Op1.addr:
            # TODO: check is there better way to determine is the function a wrapper
            v = dis.Op1.addr
            return v

    # noinspection PyMethodMayBeStatic
    def _check_is_push_retn_wrapper(self, dis0, dis1):
        """
        Checks for sequence of push IMM32/retn
        :param dis0: the first insn
        :param dis1: the second insn
        :return: value of IMM32
        """
        if dis0.itype != idaapi.NN_push or dis0.Op1.type != idaapi.o_imm or not dis0.Op1.value:
            return

        if dis1.itype not in (idaapi.NN_retn,):
            return

        return dis0.Op1.value

    def _preprocess_api_wrappers(self, fnqty):
        rv = defaultdict(dict)

        for i in xrange(fnqty):
            fn = idaapi.getn_func(i)
            items = list(FuncItems(fn.startEA))
            if len(items) not in (1, 2):
                continue

            dis0 = decode_insn(items[0])
            if dis0 is None:
                continue
            addr = self._check_is_jmp_wrapper(dis0)

            if not addr and len(items) > 1:
                dis1 = decode_insn(items[1])
                if dis1 is not None:
                    addr = self._check_is_push_retn_wrapper(dis0, dis1)

            if not addr:
                continue

            name = idaapi.get_ea_name(addr)
            name = name.replace(idaapi.FUNC_IMPORT_PREFIX, '')
            if not name:
                continue

            for tag, names in TAGS.items():
                for tag_api in names:
                    if tag_api in name:
                        refs = list(CodeRefsTo(fn.startEA, 1))

                        for ref in refs:
                            ref_fn = idaapi.get_func(ref)
                            if not ref_fn:
                                # idaapi.msg('AutoRE: there is no func for ref: %08x for api: %s' % (ref, name))
                                continue
                            if tag not in rv[ref_fn.startEA]:
                                rv[ref_fn.startEA][tag] = list()
                            if name not in rv[ref_fn.startEA][tag]:
                                rv[ref_fn.startEA][tag].append(name)
        return dict(rv)

    def run(self, arg):
        if RDEBUG and RDEBUG_EGG:
            if not os.path.isfile(RDEBUG_EGG):
                idaapi.msg('AutoRE: Remote debug is enabled, but I cannot find the debug egg: %s' % RDEBUG_EGG)
            else:
                import sys

                if RDEBUG_EGG not in sys.path:
                    sys.path.append(RDEBUG_EGG)

                import pydevd
                pydevd.settrace(RDEBUG_HOST, port=RDEBUG_PORT, stdoutToServer=True, stderrToServer=True)  # , stdoutToServer=True, stderrToServer=True

        try:
            self._data = dict()
            count = idaapi.get_func_qty()

            # pre-process of api wrapper functions
            known_refs_tags = self._preprocess_api_wrappers(count)

            for i in xrange(count):
                fn = idaapi.getn_func(i)
                fn_an = self.analyze_func(fn)

                # if fn_an['math']:
                # 	print 'fn: %#08x has math' % fn.startEA

                if idaapi.has_dummy_name(idaapi.getFlags(fn.startEA)):
                    self._handle_calls(fn, fn_an)

                known_refs = known_refs_tags.get(fn.startEA)
                self._handle_tags(fn, fn_an, known_refs)

            if self.view:
                self.view.Close(idaapi.PluginForm.FORM_NO_CONTEXT)
            self.view = AutoREView(self._data)
            self.view.Show()
        except:
            idaapi.msg('AutoRE: error: %s\n' % traceback.format_exc())

    def term(self):
        self._data = None

    @classmethod
    def disasm_func(cls, fn):
        rv = list()
        items = list(FuncItems(fn.startEA))
        for item_ea in items:
            obj = {'ea': item_ea, 'fn_ea': fn.startEA, 'dis': None}
            insn = decode_insn(item_ea)
            if insn is not None:
                obj['dis'] = insn
            rv.append(obj)
        return rv

    @classmethod
    def _analysis_handle_call_insn(cls, dis, rv):
        rv['calls'].append(dis)
        if dis.Op1.type != o_mem or not dis.Op1.addr:
            return

        name = idaapi.get_ea_name(dis.Op1.addr)
        name = name.replace(idaapi.FUNC_IMPORT_PREFIX, '')

        if '@' in name:
            name = name.split('@')[0]

        if not name:
            return

        if name in IGNORE_CALL_LIST:
            rv['calls'].pop()
            return

        if name in TAGS_IGNORE_LIST:
            return

        for tag, names in TAGS.items():
            for tag_api in names:
                if tag_api in name and name not in rv['tags'][tag]:
                    # print '%#08x: %s, tag: %s' % (dis.ea, name, tag)
                    rv['tags'][tag].append(name)
                    break

    @classmethod
    def analyze_func(cls, fn):
        rv = {'fn': fn, 'calls': [], 'math': [], 'has_bads': False, 'tags': defaultdict(list)}
        items = cls.disasm_func(fn)

        for item in items:
            dis = item['dis']
            if dis is None:
                rv['has_bads'] = True
                continue

            if dis.itype in (idaapi.NN_call, idaapi.NN_callfi, idaapi.NN_callni):
                cls._analysis_handle_call_insn(dis, rv)
            elif dis.itype == idaapi.NN_xor:
                if dis.Op1.type == o_reg and dis.Op2.type == o_reg and dis.Op1.reg == dis.Op2.reg:
                    continue
                rv['math'].append(dis)
            elif dis.itype in (idaapi.NN_shr, idaapi.NN_shl, idaapi.NN_sal, idaapi.NN_sar, idaapi.NN_ror,
                               idaapi.NN_rol, idaapi.NN_rcl, idaapi.NN_rcl):
                # TODO
                rv['math'].append(dis)

        return rv

    @classmethod
    def normalize_name(cls, n):
        for repl in replacements:
            n = n.replace(*repl)
        if '@' in n:
            n = n.split('@')[0]
        if len(n) < 3:
            return ''
        if not n.startswith(cls._PREFIX_NAME):
            n = cls._PREFIX_NAME + n
        return n

    # @classmethod
    # def mark_position(cls, ea, name, slot=[0]):
    #     curloc = idaapi.curloc()
    #     curloc.ea = ea
    #     curloc.lnnum = 0
    #     curloc.x = 0
    #     curloc.y = 0
    #     slot[0] += 1
    #     curloc.mark(slot[0], name, name)


# noinspection PyPep8Naming
def PLUGIN_ENTRY():
    return auto_re_t()
