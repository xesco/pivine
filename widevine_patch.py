#!/usr/bin/env python3
"""
widevine_patch.py — patch ChromeOS libwidevinecdm.so for Linux/Raspberry Pi aarch64.

Usage:
    python3 widevine_patch.py [--debug] input.so output.so

What it does (in order):
  1. Parses the ELF and validates every loader-visible pointer.
  2. Adds GLIBC_ABI_DT_RELR to the dynamic string table and version-needs table,
     updates DT_STRTAB / DT_STRSZ / DT_VERNEED in the dynamic section.
  3. Injects tiny aarch64 stubs for __aarch64_ldadd4_acq_rel and
     __aarch64_swp4_acq_rel, rewrites their PLT relocations.
  4. If no loader-mapped slack is available for the new data, inserts bytes and
     adjusts all affected offsets.
"""

import struct
import sys

# ── CLI ───────────────────────────────────────────────────────────────────────

debug = False
args = sys.argv[1:]
if args and args[0] == '--debug':
    debug = True
    args = args[1:]

if len(args) != 2:
    print(f'Usage: {sys.argv[0]} [--debug] input.so output.so')
    sys.exit(1)

IN, OUT = args[0], args[1]

def dbg(*a):
    if debug:
        print(*a)

# ── Raw ELF helpers (struct-based, no live views) ────────────────────────────

def u8(b, off):  return struct.unpack_from('<B', b, off)[0]
def u16(b, off): return struct.unpack_from('<H', b, off)[0]
def u32(b, off): return struct.unpack_from('<I', b, off)[0]
def u64(b, off): return struct.unpack_from('<Q', b, off)[0]
def i64(b, off): return struct.unpack_from('<q', b, off)[0]

def w16(b, off, v): struct.pack_into('<H', b, off, v)
def w32(b, off, v): struct.pack_into('<I', b, off, v)
def w64(b, off, v): struct.pack_into('<Q', b, off, v)

def cstr(b, off):
    end = bytes(b).index(b'\x00', off)
    return bytes(b[off:end])

# ── ELF constants ─────────────────────────────────────────────────────────────

PT_LOAD    = 1
PT_DYNAMIC = 2
PF_X       = 0x1
PF_W       = 0x2
PF_R       = 0x4

SHT_NULL     = 0
SHT_STRTAB   = 3
SHT_DYNSYM   = 11
SHT_GNU_VERNEED = 0x6ffffffe

DT_NULL      = 0
DT_NEEDED    = 1
DT_PLTRELSZ  = 2
DT_STRTAB    = 5
DT_SYMTAB    = 6
DT_RELA      = 7
DT_RELASZ    = 8
DT_RELAENT   = 9
DT_STRSZ     = 10
DT_SONAME    = 14
DT_JMPREL    = 23
DT_RELR      = 0x24
DT_VERSYM    = 0x6ffffff0
DT_VERNEED   = 0x6ffffffe
DT_VERNEEDNUM = 0x6fffffff

R_AARCH64_RELATIVE = 1027
R_AARCH64_JUMP_SLOT = 1026

# ── ELF64 field offsets ───────────────────────────────────────────────────────
# Ehdr
E_PHOFF    = 32
E_SHOFF    = 40
E_PHENTSIZE = 54
E_PHNUM    = 56
E_SHENTSIZE = 58
E_SHNUM    = 60
E_SHSTRNDX  = 62

# Phdr
PH_TYPE    = 0
PH_FLAGS   = 4
PH_OFFSET  = 8
PH_VADDR   = 16
PH_FILESZ  = 32
PH_MEMSZ   = 40
PH_ALIGN   = 48
PHDR_SIZE  = 56

# Shdr
SH_NAME    = 0
SH_TYPE    = 4
SH_FLAGS   = 8
SH_ADDR    = 16
SH_OFFSET  = 24
SH_SIZE    = 32
SH_LINK    = 40
SH_INFO    = 44
SH_ADDRALIGN = 48
SH_ENTSIZE  = 56
SHDR_SIZE  = 64

# Dyn
DYN_TAG    = 0
DYN_VAL    = 8
DYN_SIZE   = 16

# Sym
SYM_NAME   = 0
SYM_INFO   = 4
SYM_OTHER  = 5
SYM_SHNDX  = 6
SYM_VALUE  = 8
SYM_SIZE   = 16
SYM_ENT    = 24

# Rela
RELA_OFFSET = 0
RELA_INFO   = 8    # high 32 bits = symbol index, low 32 bits = type
RELA_ADDEND = 16
RELA_SIZE   = 24

# Verneed
VN_VERSION = 0
VN_CNT     = 2
VN_FILE    = 4
VN_AUX     = 8
VN_NEXT    = 12
VN_SIZE    = 16

# Vernaux
VNA_HASH   = 0
VNA_FLAGS  = 4
VNA_OTHER  = 6
VNA_NAME   = 8
VNA_NEXT   = 12
VNA_SIZE   = 16

# ── Load binary ───────────────────────────────────────────────────────────────

with open(IN, 'rb') as f:
    elf = bytearray(f.read())

assert elf[:4] == b'\x7fELF', 'Not an ELF file'
assert elf[4] == 2, 'Not ELF64'
assert elf[5] == 1, 'Not little-endian'

print(f'Input:  {IN}  ({len(elf)} bytes)')

# ── Parse program headers ─────────────────────────────────────────────────────

phoff    = u64(elf, E_PHOFF)
phentsz  = u16(elf, E_PHENTSIZE)
phnum    = u16(elf, E_PHNUM)

def phdr(i):
    return phoff + i * phentsz

loads   = []   # (index, p_offset, p_vaddr, p_filesz, p_memsz, p_align, p_flags)
dyn_phdr = None

for i in range(phnum):
    base = phdr(i)
    p_type   = u32(elf, base + PH_TYPE)
    p_flags  = u32(elf, base + PH_FLAGS)
    p_offset = u64(elf, base + PH_OFFSET)
    p_vaddr  = u64(elf, base + PH_VADDR)
    p_filesz = u64(elf, base + PH_FILESZ)
    p_memsz  = u64(elf, base + PH_MEMSZ)
    p_align  = u64(elf, base + PH_ALIGN)
    if p_type == PT_LOAD:
        loads.append((i, p_offset, p_vaddr, p_filesz, p_memsz, p_align, p_flags))
        dbg(f'  LOAD[{i}] off={hex(p_offset)} va={hex(p_vaddr)} '
            f'filesz={hex(p_filesz)} memsz={hex(p_memsz)} '
            f'align={hex(p_align)} flags={hex(p_flags)}')
    elif p_type == PT_DYNAMIC:
        dyn_phdr = base
        dbg(f'  DYNAMIC phdr at phdr_offset={hex(base)} '
            f'p_offset={hex(p_offset)} filesz={hex(p_filesz)}')

assert dyn_phdr is not None, 'No PT_DYNAMIC segment'

def va_to_file(va):
    """Convert a virtual address to a file offset using PT_LOAD mappings."""
    for _i, p_off, p_va, p_fs, _ms, _al, _fl in loads:
        if p_va <= va < p_va + p_fs:
            return p_off + (va - p_va)
    return None

def file_to_va(off):
    """Convert a file offset to a virtual address using PT_LOAD mappings."""
    for _i, p_off, p_va, p_fs, _ms, _al, _fl in loads:
        if p_off <= off < p_off + p_fs:
            return p_va + (off - p_off)
    return None

def is_va_mapped(va, size=1):
    for _i, p_off, p_va, p_fs, _ms, _al, _fl in loads:
        if p_va <= va and va + size <= p_va + p_fs:
            return True
    return False

# ── Parse dynamic section ─────────────────────────────────────────────────────

dyn_file_off = u64(elf, dyn_phdr + PH_OFFSET)
dyn_filesz   = u64(elf, dyn_phdr + PH_FILESZ)

dyn_tags = {}   # tag → (file_offset_of_entry, value)
p = dyn_file_off
while p < dyn_file_off + dyn_filesz:
    tag = u64(elf, p + DYN_TAG)
    val = u64(elf, p + DYN_VAL)
    dyn_tags[tag] = (p, val)
    if tag == DT_NULL:
        break
    p += DYN_SIZE

def dyn_val(tag):
    return dyn_tags[tag][1] if tag in dyn_tags else None

def set_dyn(tag, new_val):
    assert tag in dyn_tags, f'DT tag {hex(tag)} not in dynamic section'
    off = dyn_tags[tag][0]
    w64(elf, off + DYN_VAL, new_val)
    dyn_tags[tag] = (off, new_val)
    dbg(f'  set DT {hex(tag)} = {hex(new_val)}')

dbg('\n--- Dynamic section (loader-visible) ---')
for tag, (off, val) in sorted(dyn_tags.items()):
    mapped = ''
    if tag in (DT_STRTAB, DT_SYMTAB, DT_RELA, DT_JMPREL, DT_VERSYM,
               DT_VERNEED, DT_RELR):
        mapped = '  MAPPED' if is_va_mapped(val) else '  *** NOT MAPPED ***'
    dbg(f'  [{hex(off)}] tag={hex(tag):>16}  val={hex(val)}{mapped}')

# ── Read loader-visible dynstr ────────────────────────────────────────────────

strtab_va  = dyn_val(DT_STRTAB)
strtab_sz  = dyn_val(DT_STRSZ)
assert strtab_va is not None and strtab_sz is not None, 'Missing DT_STRTAB/DT_STRSZ'

strtab_file = va_to_file(strtab_va)
assert strtab_file is not None, \
    f'DT_STRTAB va={hex(strtab_va)} does not map to any PT_LOAD segment'

print(f'\nDT_STRTAB  va={hex(strtab_va)} file={hex(strtab_file)} size={strtab_sz}')

def dynstr_get(off):
    """Resolve a name from the loader-visible dynamic string table."""
    return cstr(elf, strtab_file + off)

# ── Read loader-visible .gnu.version_r ────────────────────────────────────────

verneed_va = dyn_val(DT_VERNEED)
verneed_num = dyn_val(DT_VERNEEDNUM)
assert verneed_va is not None, 'Missing DT_VERNEED'

verneed_file = va_to_file(verneed_va)
assert verneed_file is not None, \
    f'DT_VERNEED va={hex(verneed_va)} does not map to any PT_LOAD segment'

print(f'DT_VERNEED va={hex(verneed_va)} file={hex(verneed_file)} cnt={verneed_num}')

dbg('\n--- Version needs (loader-visible) ---')
all_vna_other = []
has_relr = False
vn_file_ptr = verneed_file
vn_entry_count = 0
while True:
    vn_version = u16(elf, vn_file_ptr + VN_VERSION)
    vn_cnt     = u16(elf, vn_file_ptr + VN_CNT)
    vn_file    = u32(elf, vn_file_ptr + VN_FILE)
    vn_aux     = u32(elf, vn_file_ptr + VN_AUX)
    vn_next    = u32(elf, vn_file_ptr + VN_NEXT)
    lib_name   = dynstr_get(vn_file)
    dbg(f'  Verneed @ file={hex(vn_file_ptr)}: lib={lib_name} cnt={vn_cnt}')
    vna_ptr = vn_file_ptr + vn_aux
    for _ in range(vn_cnt):
        vna_hash  = u32(elf, vna_ptr + VNA_HASH)
        vna_flags = u16(elf, vna_ptr + VNA_FLAGS)
        vna_other = u16(elf, vna_ptr + VNA_OTHER)
        vna_name  = u32(elf, vna_ptr + VNA_NAME)
        vna_next  = u32(elf, vna_ptr + VNA_NEXT)
        ver_name  = dynstr_get(vna_name)
        all_vna_other.append(vna_other)
        if ver_name == b'GLIBC_ABI_DT_RELR':
            has_relr = True
        dbg(f'    Vernaux @ file={hex(vna_ptr)}: name={ver_name} '
            f'other={vna_other} flags={vna_flags}')
        if vna_next == 0:
            break
        vna_ptr += vna_next
    vn_entry_count += 1
    if vn_next == 0:
        break
    vn_file_ptr += vn_next

print(f'  has_relr={has_relr}  vernaux_others={sorted(all_vna_other)}')

# ── Find .rela.plt entries for the two atomics ────────────────────────────────

jmprel_va  = dyn_val(DT_JMPREL)
jmprel_sz  = dyn_tags.get(0x2, (None, None))[1]  # PLTRELSZ tag = 2
# Re-read PLTRELSZ properly
jmprel_sz  = dyn_val(DT_PLTRELSZ) if DT_PLTRELSZ in dyn_tags else None
jmprel_file = va_to_file(jmprel_va) if jmprel_va else None

symtab_va   = dyn_val(DT_SYMTAB)
symtab_file = va_to_file(symtab_va) if symtab_va else None

KNOWN_ATOMICS = (b'__aarch64_ldadd4_acq_rel', b'__aarch64_swp4_acq_rel')

atomic_rela = {}        # name → file offset of Rela entry
unknown_undef = []      # undefined PLT symbols we have no stub for
if jmprel_file and jmprel_sz and symtab_file:
    dbg(f'\n--- .rela.plt  file={hex(jmprel_file)} size={hex(jmprel_sz)} ---')
    for off in range(jmprel_file, jmprel_file + jmprel_sz, RELA_SIZE):
        r_offset = u64(elf, off + RELA_OFFSET)
        r_info   = u64(elf, off + RELA_INFO)
        r_addend = i64(elf, off + RELA_ADDEND)
        r_sym    = r_info >> 32
        r_type   = r_info & 0xffffffff
        sym_off  = symtab_file + r_sym * SYM_ENT
        sym_name_off = u32(elf, sym_off + SYM_NAME)
        sym_shndx    = u16(elf, sym_off + SYM_SHNDX)
        sym_name = dynstr_get(sym_name_off)
        dbg(f'  [{hex(off)}] type={r_type} sym={r_sym} ({sym_name}) '
            f'r_offset={hex(r_offset)} addend={hex(r_addend & 0xffffffffffffffff)}')
        if sym_name in KNOWN_ATOMICS:
            atomic_rela[sym_name] = off
        elif r_type == R_AARCH64_JUMP_SLOT and sym_shndx == 0 and sym_name:
            # Undefined symbol in PLT that we have no stub for — may cause a
            # load-time failure if the symbol is absent on the target system.
            unknown_undef.append(sym_name)

print(f'\nAtomic PLT entries found: {list(atomic_rela.keys())}')
if unknown_undef:
    print(f'\nWARNING: {len(unknown_undef)} undefined PLT symbol(s) have no stub '
          f'and will be resolved at runtime by the dynamic linker:')
    for name in unknown_undef:
        print(f'  {name.decode(errors="replace")}')
    print('  If any of these are missing on the target system the CDM will '
          'fail to load. The patch may need updating.')

# ═════════════════════════════════════════════════════════════════════════════
# Phase 2 — Where do we put new data?
#
# Strategy: LOAD0 (the text segment, p_offset=0, p_vaddr=0) has no gap after
# it in the original binary — LOAD1 starts immediately at LOAD0.filesz.
# We insert new bytes at exactly that boundary (file offset = LOAD0.filesz),
# then extend LOAD0.p_filesz to cover the inserted region.
# Because LOAD0 maps va == file_offset (p_offset=0, p_vaddr=0), the new data
# is accessible at virtual addresses equal to their file offsets.
# After insertion we must bump every Phdr.p_offset and Shdr.sh_offset that
# was >= the insertion point, and also e_shoff.
# ═════════════════════════════════════════════════════════════════════════════

# Find LOAD0 (first PT_LOAD, p_offset==0)
load0 = None
for entry in loads:
    if entry[1] == 0:   # p_offset == 0
        load0 = entry
        break
assert load0 is not None, 'Could not find LOAD0 (p_offset==0)'

load0_idx, l0_off, l0_va, l0_filesz, l0_memsz, l0_align, l0_flags = load0

# Insertion point: right after end of LOAD0 in the file
insert_at = l0_off + l0_filesz
print(f'\nInsertion point: file offset {hex(insert_at)}  (end of LOAD0)')

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2a — read original loader-visible dynstr and version_r
# ─────────────────────────────────────────────────────────────────────────────

orig_dynstr = bytes(elf[strtab_file : strtab_file + strtab_sz])
orig_vr_file = verneed_file

# Walk the original version-need chain to measure its total byte size
vp = orig_vr_file
while True:
    vn_next = u32(elf, vp + VN_NEXT)
    vn_cnt  = u16(elf, vp + VN_CNT)
    vap = vp + u32(elf, vp + VN_AUX)
    for _ in range(vn_cnt):
        vna_next = u32(elf, vap + VNA_NEXT)
        if vna_next == 0:
            vap += VNA_SIZE
            break
        vap += vna_next
    if vn_next == 0:
        orig_vr_end = vap   # file offset just past last Vernaux
        break
    vp += vn_next

orig_vr_size = orig_vr_end - orig_vr_file
dbg(f'\nOriginal version_r chain: file [{hex(orig_vr_file)}, {hex(orig_vr_end)})  size={hex(orig_vr_size)}')

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2b — build new dynstr blob
# ─────────────────────────────────────────────────────────────────────────────

RELR_STR = b'GLIBC_ABI_DT_RELR\x00'

new_dynstr = bytearray(orig_dynstr)
relr_name_off = len(new_dynstr)   # offset of GLIBC_ABI_DT_RELR in new table
new_dynstr += RELR_STR
new_dynstr_size = len(new_dynstr)

print(f'\nNew dynstr: {len(orig_dynstr)} → {new_dynstr_size} bytes')
print(f'  GLIBC_ABI_DT_RELR at dynstr offset {relr_name_off}')

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2c — build new version_r blob
#
# Copy the entire chain verbatim from the loader-visible location,
# then append a new Vernaux for GLIBC_ABI_DT_RELR to the libc.so.6 entry.
# ─────────────────────────────────────────────────────────────────────────────

# ELF_hash of "GLIBC_ABI_DT_RELR" (sysv hash)
def elf_hash(name: bytes) -> int:
    h = 0
    for c in name:
        h = (h << 4) + c
        g = h & 0xf0000000
        if g:
            h ^= g >> 24
        h &= ~g
    return h & 0xffffffff

RELR_HASH = elf_hash(b'GLIBC_ABI_DT_RELR')
new_vna_other = max(all_vna_other) + 1

print(f'  GLIBC_ABI_DT_RELR hash={hex(RELR_HASH)} vna_other={new_vna_other}')

new_vr = bytearray(bytes(elf[orig_vr_file : orig_vr_end]))  # verbatim copy

# Walk new_vr to find libc.so.6 Verneed and append the new Vernaux
vp = 0
while True:
    vn_cnt  = u16(new_vr, vp + VN_CNT)
    vn_file = u32(new_vr, vp + VN_FILE)
    vn_aux  = u32(new_vr, vp + VN_AUX)
    vn_next = u32(new_vr, vp + VN_NEXT)
    lib = cstr(new_dynstr, vn_file)   # offsets into orig_dynstr still valid
    if lib == b'libc.so.6':
        # Walk to the last Vernaux
        vap = vp + vn_aux
        last_vap = vap
        for _ in range(vn_cnt):
            vna_next = u32(new_vr, vap + VNA_NEXT)
            if vna_next == 0:
                last_vap = vap
                break
            vap += vna_next
        # Append a new Vernaux entry at the end of new_vr
        new_vna = bytearray(VNA_SIZE)
        w32(new_vna, VNA_HASH,  RELR_HASH)
        w16(new_vna, VNA_FLAGS, 0)
        w16(new_vna, VNA_OTHER, new_vna_other)
        w32(new_vna, VNA_NAME,  relr_name_off)
        w32(new_vna, VNA_NEXT,  0)
        new_entry_off = len(new_vr)
        w32(new_vr, last_vap + VNA_NEXT, new_entry_off - last_vap)
        new_vr += new_vna
        w16(new_vr, vp + VN_CNT, vn_cnt + 1)
        dbg(f'  Appended GLIBC_ABI_DT_RELR Vernaux at new_vr offset {hex(new_entry_off)}')
        break
    if vn_next == 0:
        break
    vp += vn_next

print(f'New version_r: {orig_vr_size} → {len(new_vr)} bytes')

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2d — build the stub code
# ─────────────────────────────────────────────────────────────────────────────

LDADD_STUB = bytes.fromhex('e203002a20fc5f880300020b23fc0488a4ffff35c0035fd6')
SWP_STUB   = bytes.fromhex('e203002a20fc5f8822fc0388c3ffff35c0035fd6')

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2e — lay out the insertion blob
#
# Layout inside the inserted region (all at file offsets >= insert_at):
#   [align 8] new dynstr
#   [align 4] new version_r
#   [align 4] ldadd stub
#   [align 4] swp stub
#   [align 8] tail padding to 8-byte boundary
#
# Because LOAD0 maps va == file_offset, va == file offset for everything here.
# ─────────────────────────────────────────────────────────────────────────────

def layout_blob():
    """Build the insertion blob and return (blob, dynstr_off, vr_off, ldadd_off, swp_off).
    All offsets are relative to insert_at (i.e., offsets within the blob).

    The blob size MUST be a multiple of l0_align (the PT_LOAD alignment, 0x10000).
    This preserves the invariant  p_offset % p_align == p_vaddr % p_align  for every
    shifted segment, which the kernel ELF loader requires.
    """
    b = bytearray()

    def align_to(n):
        pad = (-len(b)) % n
        b.extend(b'\x00' * pad)

    align_to(8)
    d_off = len(b)
    b += new_dynstr

    align_to(4)
    vr_off = len(b)
    b += new_vr

    align_to(4)
    la_off = len(b)
    b += LDADD_STUB

    align_to(4)
    sw_off = len(b)
    b += SWP_STUB

    # Pad to a multiple of the segment alignment so all shifted PT_LOAD entries
    # remain page-congruent (p_offset % align == p_vaddr % align).
    align_to(l0_align)
    return bytes(b), d_off, vr_off, la_off, sw_off

blob, blob_dynstr_off, blob_vr_off, blob_ldadd_off, blob_swp_off = layout_blob()
insert_size = len(blob)

print(f'\nInserting {insert_size} ({hex(insert_size)}) bytes at file offset {hex(insert_at)}')
print(f'  new dynstr  blob[{hex(blob_dynstr_off)}]  va={hex(insert_at + blob_dynstr_off)}')
print(f'  new vr      blob[{hex(blob_vr_off)}]  va={hex(insert_at + blob_vr_off)}')
print(f'  ldadd stub  blob[{hex(blob_ldadd_off)}]  va={hex(insert_at + blob_ldadd_off)}')
print(f'  swp stub    blob[{hex(blob_swp_off)}]  va={hex(insert_at + blob_swp_off)}')

new_dynstr_file = insert_at + blob_dynstr_off
new_vr_file     = insert_at + blob_vr_off
ldadd_file      = insert_at + blob_ldadd_off
swp_file        = insert_at + blob_swp_off
# LOAD0: va == file offset, so:
ldadd_va = ldadd_file
swp_va   = swp_file

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2f — do the insertion into the bytearray
# ─────────────────────────────────────────────────────────────────────────────

elf[insert_at:insert_at] = blob   # splice blob into bytearray

# After insertion, any field that stored a file offset >= insert_at must be bumped.
# We do this BEFORE editing anything else so offsets are still consistent.

def bump_offset(current_off):
    """If a stored file offset was >= insert_at, return it bumped by insert_size."""
    return current_off + insert_size if current_off >= insert_at else current_off

# Bump e_shoff
old_shoff = u64(elf, E_SHOFF)
new_shoff = bump_offset(old_shoff)
w64(elf, E_SHOFF, new_shoff)
dbg(f'  e_shoff: {hex(old_shoff)} → {hex(new_shoff)}')

# Bump phdr p_offset for every phdr whose p_offset >= insert_at
for i in range(phnum):
    base = phoff + i * phentsz   # phoff itself is < insert_at (near start of file)
    p_type   = u32(elf, base + PH_TYPE)
    p_off_v  = u64(elf, base + PH_OFFSET)
    if p_off_v >= insert_at:
        new_p_off = p_off_v + insert_size
        w64(elf, base + PH_OFFSET, new_p_off)
        dbg(f'  phdr[{i}] type={hex(p_type)} p_offset: {hex(p_off_v)} → {hex(new_p_off)}')

# Bump shdr sh_offset for every shdr whose sh_offset >= insert_at
shoff_new  = u64(elf, E_SHOFF)   # use the already-updated value
shentsz_   = u16(elf, E_SHENTSIZE)
shnum_     = u16(elf, E_SHNUM)
for i in range(shnum_):
    sh_base  = shoff_new + i * shentsz_
    sh_off_v = u64(elf, sh_base + SH_OFFSET)
    if sh_off_v >= insert_at:
        new_sh_off = sh_off_v + insert_size
        w64(elf, sh_base + SH_OFFSET, new_sh_off)
        dbg(f'  shdr[{i}] sh_offset: {hex(sh_off_v)} → {hex(new_sh_off)}')

# Extend LOAD0 p_filesz / p_memsz
load0_phdr_base = phoff + load0_idx * phentsz
old_filesz = u64(elf, load0_phdr_base + PH_FILESZ)
new_filesz = old_filesz + insert_size
w64(elf, load0_phdr_base + PH_FILESZ, new_filesz)
w64(elf, load0_phdr_base + PH_MEMSZ,  new_filesz)
print(f'Extended LOAD0 p_filesz {hex(old_filesz)} → {hex(new_filesz)}')

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Rewrite .rela.plt for the two atomic helpers
#
# The stub code was already inserted in Phase 2f as part of the blob.
# Now rewrite the PLT relocations to R_AARCH64_RELATIVE pointing at the stubs.
# Note: after the insertion, all file offsets >= insert_at were bumped.
# jmprel_file was computed before insertion, so bump it if needed.
# ─────────────────────────────────────────────────────────────────────────────

# Re-read jmprel and symtab file offsets (they may have been shifted by insertion)
jmprel_file_new = bump_offset(jmprel_file) if jmprel_file else None
symtab_file_new = bump_offset(symtab_file) if symtab_file else None

# dynstr was also shifted — but new_dynstr_file already points to the new copy.
# For PLT walk we still need to look up symbol names from the NEW dynstr.
# DT_STRTAB hasn't been updated yet (we do that below), but we know the new location.
def dynstr_get_new(off):
    return cstr(elf, new_dynstr_file + off)

print(f'\n--- Rewriting atomic PLT relocs ---')
LDADD = b'__aarch64_ldadd4_acq_rel'
SWP   = b'__aarch64_swp4_acq_rel'
stub_va = {LDADD: ldadd_va, SWP: swp_va}
patched_atomics = set()

if jmprel_file_new and jmprel_sz and symtab_file_new:
    for off in range(jmprel_file_new, jmprel_file_new + jmprel_sz, RELA_SIZE):
        r_info   = u64(elf, off + RELA_INFO)
        r_sym    = r_info >> 32
        sym_off  = symtab_file_new + r_sym * SYM_ENT
        sym_name_off = u32(elf, sym_off + SYM_NAME)
        sym_name = dynstr_get_new(sym_name_off)
        if sym_name in stub_va:
            new_addend = stub_va[sym_name]
            new_info   = (0 << 32) | R_AARCH64_RELATIVE
            w64(elf, off + RELA_INFO,   new_info)
            w64(elf, off + RELA_ADDEND, new_addend & 0xffffffffffffffff)
            patched_atomics.add(sym_name)
            print(f'  Patched {sym_name.decode()} PLT reloc → R_AARCH64_RELATIVE addend={hex(new_addend)}')

if len(patched_atomics) != 2:
    print(f'WARNING: expected 2 atomic relocs, patched {len(patched_atomics)}: {patched_atomics}')

# Make the dynsym entries WEAK so the linker doesn't hard-fail if they resolve to 0.
if symtab_file_new:
    shoff_v  = u64(elf, E_SHOFF)
    shentsz_ = u16(elf, E_SHENTSIZE)
    shnum_   = u16(elf, E_SHNUM)
    dynsym_size = None
    for _si in range(shnum_):
        sh_base = shoff_v + _si * shentsz_
        sh_type = u32(elf, sh_base + SH_TYPE)
        sh_addr = u64(elf, sh_base + SH_ADDR)
        sh_sz   = u64(elf, sh_base + SH_SIZE)
        if sh_type == SHT_DYNSYM and sh_addr == symtab_va:
            dynsym_size = sh_sz
            dbg(f'  Found SHT_DYNSYM: addr={hex(sh_addr)} size={hex(sh_sz)}')
            break
    if dynsym_size is None:
        dbg('  WARNING: could not determine dynsym size from section headers; skipping weak-bind fixup')
    else:
        sym_count = dynsym_size // SYM_ENT
        for _si in range(sym_count):
            sym_off  = symtab_file_new + _si * SYM_ENT
            sym_name_off = u32(elf, sym_off + SYM_NAME)
            try:
                sym_name = dynstr_get_new(sym_name_off)
            except Exception:
                continue
            if sym_name in stub_va:
                st_info = u8(elf, sym_off + SYM_INFO)
                new_info = (2 << 4) | (st_info & 0x0f)  # STB_WEAK
                elf[sym_off + SYM_INFO] = new_info
                dbg(f'  Set STB_WEAK on dynsym {sym_name}')

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — Update dynamic tags to point at the new blobs
# ─────────────────────────────────────────────────────────────────────────────
# After insertion, the dynamic section itself was shifted (it lives in LOAD1).
# But dyn_tags[] stores the file offsets of each entry, which we bumped above.
# Re-read the dyn_tags offsets from the now-updated elf bytearray.

def rebuild_dyn_tags():
    global dyn_tags
    dyn_tags = {}
    dyn_off_new = bump_offset(dyn_file_off)
    dyn_sz_new  = dyn_filesz   # size unchanged
    p = dyn_off_new
    while p < dyn_off_new + dyn_sz_new:
        tag = u64(elf, p + DYN_TAG)
        val = u64(elf, p + DYN_VAL)
        dyn_tags[tag] = (p, val)
        if tag == DT_NULL:
            break
        p += DYN_SIZE

rebuild_dyn_tags()

set_dyn(DT_STRTAB, new_dynstr_file)   # va == file offset for LOAD0
set_dyn(DT_STRSZ,  new_dynstr_size)
set_dyn(DT_VERNEED, new_vr_file)       # va == file offset for LOAD0
# DT_VERNEEDNUM stays the same (same number of Verneed entries)

print(f'\nUpdated dynamic section:')
print(f'  DT_STRTAB  = {hex(new_dynstr_file)}')
print(f'  DT_STRSZ   = {new_dynstr_size}')
print(f'  DT_VERNEED = {hex(new_vr_file)}')

# ─────────────────────────────────────────────────────────────────────────────
# Verification pass
# ─────────────────────────────────────────────────────────────────────────────

print('\n--- Verification ---')

# Rebuild loads list from updated phdr
loads_final = []
for i in range(phnum):
    base_ = phoff + i * phentsz
    p_type_  = u32(elf, base_ + PH_TYPE)
    p_off_   = u64(elf, base_ + PH_OFFSET)
    p_va_    = u64(elf, base_ + PH_VADDR)
    p_fs_    = u64(elf, base_ + PH_FILESZ)
    p_ms_    = u64(elf, base_ + PH_MEMSZ)
    if p_type_ == PT_LOAD:
        loads_final.append((p_off_, p_va_, p_fs_, p_ms_))

def is_va_mapped_final(va, size=1):
    for p_off_, p_va_, p_fs_, _ in loads_final:
        if p_va_ <= va and va + size <= p_va_ + p_fs_:
            return True
    return False

ok = True
for name, va, size in [
    ('new dynstr',    new_dynstr_file, new_dynstr_size),
    ('new version_r', new_vr_file,     len(new_vr)),
    ('ldadd stub',    ldadd_va,        len(LDADD_STUB)),
    ('swp stub',      swp_va,          len(SWP_STUB)),
]:
    mapped = is_va_mapped_final(va, size)
    status = 'OK' if mapped else 'FAIL *** NOT MAPPED ***'
    print(f'  {name:20s}  va={hex(va)} size={size}  {status}')
    if not mapped:
        ok = False

# Walk new version_r and check GLIBC_ABI_DT_RELR is present
found_relr = False
vp2 = 0
while True:
    vn_cnt  = u16(new_vr, vp2 + VN_CNT)
    vn_file = u32(new_vr, vp2 + VN_FILE)
    vn_aux  = u32(new_vr, vp2 + VN_AUX)
    vn_next = u32(new_vr, vp2 + VN_NEXT)
    lib = cstr(new_dynstr, vn_file)
    vap = vp2 + vn_aux
    for _ in range(vn_cnt):
        vna_name = u32(new_vr, vap + VNA_NAME)
        ver = cstr(new_dynstr, vna_name)
        if ver == b'GLIBC_ABI_DT_RELR':
            found_relr = True
        vna_next = u32(new_vr, vap + VNA_NEXT)
        if vna_next == 0:
            break
        vap += vna_next
    if vn_next == 0:
        break
    vp2 += vn_next

print(f'  GLIBC_ABI_DT_RELR in new version_r: {"OK" if found_relr else "FAIL"}')
print(f'  GLIBC_ABI_DT_RELR in new dynstr:     {"OK" if b"GLIBC_ABI_DT_RELR" in new_dynstr else "FAIL"}')
print(f'  Atomic relocs patched: {len(patched_atomics)}/2  {"OK" if len(patched_atomics)==2 else "FAIL"}')

if not ok or not found_relr:
    print('\nPATCH FAILED — not writing output')
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Write output
# ─────────────────────────────────────────────────────────────────────────────

with open(OUT, 'wb') as f:
    f.write(elf)

print(f'\nOutput: {OUT}  ({len(elf)} bytes)')
print('Done.')
