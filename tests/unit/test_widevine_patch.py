"""
Tests for widevine_patch.py using the minimal ELF fixture.

The patcher is a script (not a module), so it is invoked via subprocess.
"""

import struct
import subprocess
import sys
from pathlib import Path

import pytest

PATCH_SCRIPT = Path(__file__).parent.parent.parent / 'widevine_patch.py'

# Fixture location
sys.path.insert(0, str(Path(__file__).parent / 'fixtures'))
from minimal_elf import build_minimal_widevine_elf  # noqa: E402

# ── ELF struct helpers (mirrors widevine_patch.py) ────────────────────────────

def u8(b, off):  return struct.unpack_from('<B', b, off)[0]
def u16(b, off): return struct.unpack_from('<H', b, off)[0]
def u32(b, off): return struct.unpack_from('<I', b, off)[0]
def u64(b, off): return struct.unpack_from('<Q', b, off)[0]
def i64(b, off): return struct.unpack_from('<q', b, off)[0]

def cstr(b, off):
    end = bytes(b).index(b'\x00', off)
    return bytes(b[off:end])

# ELF constants
PT_LOAD             = 1
PH_TYPE, PH_FLAGS, PH_OFFSET, PH_VADDR   = 0, 4, 8, 16
PH_FILESZ, PH_MEMSZ, PH_ALIGN            = 32, 40, 48
PHDR_SIZE           = 56
E_PHOFF, E_PHENTSIZE, E_PHNUM             = 32, 54, 56
E_SHOFF, E_SHENTSIZE, E_SHNUM             = 40, 58, 60
DT_STRTAB, DT_STRSZ                       = 5, 10
DT_VERNEED, DT_VERNEEDNUM                 = 0x6ffffffe, 0x6fffffff
DT_JMPREL, DT_PLTRELSZ                   = 23, 2
DT_NULL             = 0
DYN_TAG, DYN_VAL, DYN_SIZE               = 0, 8, 16
VN_VERSION, VN_CNT, VN_FILE, VN_AUX, VN_NEXT = 0, 2, 4, 8, 12
VNA_HASH, VNA_FLAGS, VNA_OTHER, VNA_NAME, VNA_NEXT = 0, 4, 6, 8, 12
VNA_SIZE            = 16
RELA_OFFSET, RELA_INFO, RELA_ADDEND       = 0, 8, 16
RELA_SIZE           = 24
R_AARCH64_RELATIVE  = 1027
R_AARCH64_JUMP_SLOT = 1026


# ── Helpers ───────────────────────────────────────────────────────────────────

def run_patcher(src: Path, dst: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PATCH_SCRIPT), str(src), str(dst)],
        capture_output=True, text=True,
    )


def get_loads(elf: bytes):
    """Return list of (p_offset, p_vaddr, p_filesz, p_memsz, p_align) for PT_LOAD."""
    phoff   = u64(elf, E_PHOFF)
    phentsz = u16(elf, E_PHENTSIZE)
    phnum   = u16(elf, E_PHNUM)
    result = []
    for i in range(phnum):
        base = phoff + i * phentsz
        if u32(elf, base + PH_TYPE) == PT_LOAD:
            result.append((
                u64(elf, base + PH_OFFSET),
                u64(elf, base + PH_VADDR),
                u64(elf, base + PH_FILESZ),
                u64(elf, base + PH_MEMSZ),
                u64(elf, base + PH_ALIGN),
            ))
    return result


def va_to_file(elf: bytes, va: int):
    for p_off, p_va, p_fs, _, _ in get_loads(elf):
        if p_va <= va < p_va + p_fs:
            return p_off + (va - p_va)
    return None


def get_dyn_tags(elf: bytes):
    """Return dict tag → value using PT_DYNAMIC."""
    phoff   = u64(elf, E_PHOFF)
    phentsz = u16(elf, E_PHENTSIZE)
    phnum   = u16(elf, E_PHNUM)
    dyn_off = dyn_sz = None
    for i in range(phnum):
        base = phoff + i * phentsz
        if u32(elf, base + PH_TYPE) == 2:  # PT_DYNAMIC
            dyn_off = u64(elf, base + PH_OFFSET)
            dyn_sz  = u64(elf, base + PH_FILESZ)
            break
    assert dyn_off is not None
    tags = {}
    p = dyn_off
    while p < dyn_off + dyn_sz:
        tag = u64(elf, p + DYN_TAG)
        val = u64(elf, p + DYN_VAL)
        tags[tag] = val
        if tag == DT_NULL:
            break
        p += DYN_SIZE
    return tags


def read_version_needs(elf: bytes, vn_file_off: int, strtab_file_off: int):
    """Walk the Verneed chain; return list of (lib_name, [(ver_name, flags, other)])."""
    result = []
    vp = vn_file_off
    while True:
        vn_cnt  = u16(elf, vp + VN_CNT)
        vn_file = u32(elf, vp + VN_FILE)
        vn_aux  = u32(elf, vp + VN_AUX)
        vn_next = u32(elf, vp + VN_NEXT)
        lib = cstr(elf, strtab_file_off + vn_file)
        auxs = []
        vap = vp + vn_aux
        for _ in range(vn_cnt):
            vna_flags = u16(elf, vap + VNA_FLAGS)
            vna_other = u16(elf, vap + VNA_OTHER)
            vna_name  = u32(elf, vap + VNA_NAME)
            vna_next  = u32(elf, vap + VNA_NEXT)
            ver = cstr(elf, strtab_file_off + vna_name)
            auxs.append((ver, vna_flags, vna_other))
            if vna_next == 0:
                break
            vap += vna_next
        result.append((lib, auxs))
        if vn_next == 0:
            break
        vp += vn_next
    return result


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def patched(tmp_path_factory):
    """Build minimal ELF, run patcher, return (src_bytes, dst_bytes, dst_path)."""
    d = tmp_path_factory.mktemp('widevine_patch')
    src = d / 'input.so'
    dst = d / 'output.so'
    src.write_bytes(build_minimal_widevine_elf())
    result = run_patcher(src, dst)
    assert result.returncode == 0, (
        f'patcher exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}'
    )
    return src.read_bytes(), dst.read_bytes(), dst


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestBasicELF:
    def test_magic_and_class(self, patched):
        _, dst, _ = patched
        assert dst[:4] == b'\x7fELF', 'ELF magic missing'
        assert dst[4] == 2, 'Not ELF64'
        assert dst[5] == 1, 'Not little-endian'

    def test_machine_aarch64(self, patched):
        _, dst, _ = patched
        e_machine = struct.unpack_from('<H', dst, 18)[0]
        assert e_machine == 183, f'Expected EM_AARCH64 (183), got {e_machine}'


class TestLoadSegmentAlignment:
    def test_all_pt_load_page_congruent(self, patched):
        """Every PT_LOAD must satisfy (p_vaddr - p_offset) % p_align == 0."""
        _, dst, _ = patched
        loads = get_loads(dst)
        assert loads, 'No PT_LOAD segments found'
        for p_off, p_va, _fs, _ms, p_align in loads:
            if p_align > 1:
                assert (p_va - p_off) % p_align == 0, (
                    f'PT_LOAD p_offset={hex(p_off)} p_vaddr={hex(p_va)} '
                    f'p_align={hex(p_align)} not page-congruent'
                )

    def test_load0_starts_at_zero(self, patched):
        _, dst, _ = patched
        loads = get_loads(dst)
        assert loads[0][0] == 0, 'LOAD0 p_offset should be 0'
        assert loads[0][1] == 0, 'LOAD0 p_vaddr should be 0'


class TestInsertionSize:
    def test_insert_size_is_l0_align_multiple(self, patched):
        """The inserted blob must be a multiple of LOAD0's p_align."""
        src, dst, _ = patched
        src_loads = get_loads(src)
        dst_loads = get_loads(dst)
        # LOAD0 filesz difference is the insert size
        insert_size = dst_loads[0][2] - src_loads[0][2]
        l0_align = dst_loads[0][4]   # p_align of LOAD0 in patched binary
        assert insert_size > 0, 'Nothing was inserted'
        assert insert_size % l0_align == 0, (
            f'insert_size={hex(insert_size)} is not a multiple of '
            f'l0_align={hex(l0_align)}'
        )

    def test_insert_size_exactly_one_page(self, patched):
        """Insert size should be exactly one l0_align page (no over-allocation)."""
        src, dst, _ = patched
        src_loads = get_loads(src)
        dst_loads = get_loads(dst)
        insert_size = dst_loads[0][2] - src_loads[0][2]
        l0_align = dst_loads[0][4]
        assert insert_size == l0_align, (
            f'Expected insert_size == l0_align={hex(l0_align)}, '
            f'got {hex(insert_size)}'
        )


class TestDynstr:
    def test_relr_string_in_dynstr(self, patched):
        """GLIBC_ABI_DT_RELR must appear in the new dynamic string table."""
        _, dst, _ = patched
        tags = get_dyn_tags(dst)
        strtab_va  = tags[DT_STRTAB]
        strtab_sz  = tags[DT_STRSZ]
        strtab_off = va_to_file(dst, strtab_va)
        assert strtab_off is not None, 'DT_STRTAB VA not mapped'
        dynstr = dst[strtab_off : strtab_off + strtab_sz]
        assert b'GLIBC_ABI_DT_RELR\x00' in dynstr, \
            'GLIBC_ABI_DT_RELR not found in new dynstr'

    def test_dynstr_null_terminated(self, patched):
        _, dst, _ = patched
        tags = get_dyn_tags(dst)
        strtab_off = va_to_file(dst, tags[DT_STRTAB])
        strtab_sz  = tags[DT_STRSZ]
        assert dst[strtab_off + strtab_sz - 1] == 0, \
            'dynstr does not end with a null byte'


class TestVersionNeeds:
    def _get_vr(self, dst):
        tags = get_dyn_tags(dst)
        vn_va  = tags[DT_VERNEED]
        st_va  = tags[DT_STRTAB]
        vn_off = va_to_file(dst, vn_va)
        st_off = va_to_file(dst, st_va)
        assert vn_off is not None, 'DT_VERNEED VA not mapped'
        assert st_off is not None, 'DT_STRTAB VA not mapped'
        return read_version_needs(dst, vn_off, st_off)

    def test_relr_vernaux_present_under_libc(self, patched):
        _, dst, _ = patched
        needs = self._get_vr(dst)
        libc_entry = next((auxs for lib, auxs in needs if lib == b'libc.so.6'), None)
        assert libc_entry is not None, 'libc.so.6 Verneed entry not found'
        ver_names = [name for name, _flags, _other in libc_entry]
        assert b'GLIBC_ABI_DT_RELR' in ver_names, \
            f'GLIBC_ABI_DT_RELR not in libc.so.6 Vernaux; found: {ver_names}'

    def test_relr_vna_flags_zero(self, patched):
        _, dst, _ = patched
        needs = self._get_vr(dst)
        libc_entry = next(auxs for lib, auxs in needs if lib == b'libc.so.6')
        for name, flags, _other in libc_entry:
            if name == b'GLIBC_ABI_DT_RELR':
                assert flags == 0, f'Expected vna_flags=0, got {flags}'

    def test_vna_other_values_unique(self, patched):
        _, dst, _ = patched
        needs = self._get_vr(dst)
        others = [other for _lib, auxs in needs for _name, _flags, other in auxs]
        assert len(others) == len(set(others)), \
            f'Duplicate vna_other values found: {sorted(others)}'

    def test_relr_vna_other_is_max_plus_one(self, patched):
        """The new Vernaux vna_other must be max(existing)+1."""
        src, dst, _ = patched
        # Read originals from the source
        src_tags  = get_dyn_tags(src)
        src_vn_off = va_to_file(src, src_tags[DT_VERNEED])
        src_st_off = va_to_file(src, src_tags[DT_STRTAB])
        src_needs  = read_version_needs(src, src_vn_off, src_st_off)
        orig_others = [o for _lib, auxs in src_needs for _n, _f, o in auxs]

        dst_tags  = get_dyn_tags(dst)
        dst_vn_off = va_to_file(dst, dst_tags[DT_VERNEED])
        dst_st_off = va_to_file(dst, dst_tags[DT_STRTAB])
        dst_needs  = read_version_needs(dst, dst_vn_off, dst_st_off)
        libc_entry = next(auxs for lib, auxs in dst_needs if lib == b'libc.so.6')
        new_other  = next(o for name, _f, o in libc_entry if name == b'GLIBC_ABI_DT_RELR')

        assert new_other == max(orig_others) + 1, (
            f'Expected vna_other={max(orig_others)+1}, got {new_other}'
        )


class TestAtomicRelocs:
    def _get_plt_relocs(self, elf: bytes):
        """Return list of (r_type, r_addend) for all .rela.plt entries."""
        tags     = get_dyn_tags(elf)
        jmprel_va = tags.get(DT_JMPREL)
        jmprel_sz = tags.get(DT_PLTRELSZ)
        if not jmprel_va or not jmprel_sz:
            return []
        jmprel_off = va_to_file(elf, jmprel_va)
        result = []
        for off in range(jmprel_off, jmprel_off + jmprel_sz, RELA_SIZE):
            r_info   = u64(elf, off + RELA_INFO)
            r_addend = i64(elf, off + RELA_ADDEND)
            r_type   = r_info & 0xffffffff
            result.append((r_type, r_addend))
        return result

    def test_both_atomic_relocs_rewritten(self, patched):
        """Both atomic PLT entries must become R_AARCH64_RELATIVE."""
        _, dst, _ = patched
        relocs = self._get_plt_relocs(dst)
        relative_count = sum(1 for r_type, _ in relocs if r_type == R_AARCH64_RELATIVE)
        jump_slot_count = sum(1 for r_type, _ in relocs if r_type == R_AARCH64_JUMP_SLOT)
        assert relative_count == 2, \
            f'Expected 2 R_AARCH64_RELATIVE relocs, got {relative_count}'
        assert jump_slot_count == 0, \
            f'Expected 0 R_AARCH64_JUMP_SLOT relocs remaining, got {jump_slot_count}'

    def test_atomic_reloc_addends_are_mapped(self, patched):
        """The addend (stub VA) in each R_AARCH64_RELATIVE reloc must be loader-mapped."""
        _, dst, _ = patched
        relocs = self._get_plt_relocs(dst)
        loads = get_loads(dst)
        for r_type, r_addend in relocs:
            if r_type == R_AARCH64_RELATIVE:
                va = r_addend & 0xffffffffffffffff
                mapped = any(p_va <= va < p_va + p_fs for _po, p_va, p_fs, _ms, _al in loads)
                assert mapped, f'Stub VA {hex(va)} is not covered by any PT_LOAD segment'

    def test_original_has_jump_slots(self, patched):
        """Sanity-check: the source fixture should have JUMP_SLOT relocs."""
        src, _, _ = patched
        relocs = self._get_plt_relocs(src)
        jump_slot_count = sum(1 for r_type, _ in relocs if r_type == R_AARCH64_JUMP_SLOT)
        assert jump_slot_count == 2, \
            f'Expected 2 R_AARCH64_JUMP_SLOT in source, got {jump_slot_count}'


class TestNewDataCoveredByLoad:
    def test_new_dynstr_covered_by_pt_load(self, patched):
        _, dst, _ = patched
        tags = get_dyn_tags(dst)
        strtab_va = tags[DT_STRTAB]
        strtab_sz = tags[DT_STRSZ]
        loads = get_loads(dst)
        covered = any(
            p_va <= strtab_va and strtab_va + strtab_sz <= p_va + p_fs
            for _po, p_va, p_fs, _ms, _al in loads
        )
        assert covered, \
            f'New dynstr va={hex(strtab_va)} size={strtab_sz} not fully covered by PT_LOAD'

    def test_new_version_r_covered_by_pt_load(self, patched):
        _, dst, _ = patched
        tags = get_dyn_tags(dst)
        vr_va = tags[DT_VERNEED]
        loads = get_loads(dst)
        # Measure version_r size by walking it
        vr_off = va_to_file(dst, vr_va)
        st_off = va_to_file(dst, tags[DT_STRTAB])
        # Walk to find end
        vp = vr_off
        while True:
            vn_cnt  = u16(dst, vp + 2)
            vn_aux  = u32(dst, vp + 8)
            vn_next = u32(dst, vp + 12)
            vap = vp + vn_aux
            for _ in range(vn_cnt):
                vna_next = u32(dst, vap + 12)
                if vna_next == 0:
                    vap += VNA_SIZE
                    break
                vap += vna_next
            if vn_next == 0:
                vr_end_file = vap
                break
            vp += vn_next
        vr_size = vr_end_file - vr_off
        vr_end_va = vr_va + vr_size
        covered = any(
            p_va <= vr_va and vr_end_va <= p_va + p_fs
            for _po, p_va, p_fs, _ms, _al in loads
        )
        assert covered, \
            f'New version_r va={hex(vr_va)} size={vr_size} not fully covered by PT_LOAD'


class TestPatcherOutput:
    def test_output_larger_than_input(self, patched):
        src, dst, _ = patched
        assert len(dst) > len(src)

    def test_patcher_prints_verification_block(self, patched):
        src_bytes, _, dst_path = patched
        d = dst_path.parent
        src = d / 'verify_input.so'
        dst = d / 'verify_output.so'
        src.write_bytes(src_bytes)
        result = run_patcher(src, dst)
        assert '--- Verification ---' in result.stdout

    def test_patcher_fails_on_non_elf(self, tmp_path):
        bad = tmp_path / 'bad.so'
        bad.write_bytes(b'this is not an ELF file at all')
        out = tmp_path / 'out.so'
        result = run_patcher(bad, out)
        assert result.returncode != 0
        assert not out.exists(), 'Patcher should not write output on failure'


class TestUnknownUndefWarning:
    def test_warns_on_unknown_undefined_plt_symbol(self, tmp_path):
        """Patcher must warn when a JUMP_SLOT entry references an unknown undefined symbol."""
        src = tmp_path / 'input.so'
        dst = tmp_path / 'output.so'
        src.write_bytes(build_minimal_widevine_elf(
            extra_undef_symbol=b'__some_future_unknown_helper'
        ))
        result = run_patcher(src, dst)
        assert result.returncode == 0, \
            f'Patcher should still succeed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}'
        assert 'WARNING' in result.stdout, \
            'Expected a WARNING about unknown undefined PLT symbol'
        assert '__some_future_unknown_helper' in result.stdout, \
            'Expected the unknown symbol name to appear in the warning'

    def test_no_warning_when_all_symbols_known(self, tmp_path):
        """No warning should appear when the only undefined PLT symbols are the known atomics."""
        src = tmp_path / 'input.so'
        dst = tmp_path / 'output.so'
        src.write_bytes(build_minimal_widevine_elf())
        result = run_patcher(src, dst)
        assert result.returncode == 0
        assert 'WARNING' not in result.stdout, \
            f'Unexpected WARNING in output:\n{result.stdout}'
