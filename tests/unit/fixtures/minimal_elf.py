"""
Builds a minimal but structurally valid aarch64 ELF64 shared library
that mimics the layout of the ChromeOS Widevine CDM just enough for
widevine_patch.py to process it:

  - LOAD0: p_offset=0, p_vaddr=0 (text segment; va == file offset)
  - LOAD1: data segment immediately after LOAD0 in the file
  - PT_DYNAMIC pointing into LOAD1
  - .dynsym with __aarch64_ldadd4_acq_rel and __aarch64_swp4_acq_rel
  - .dynstr, .rela.plt, .gnu.version_r, .dynamic section headers
  - DT_STRTAB / DT_VERNEED / DT_SYMTAB / DT_JMPREL all within LOAD0
    (so va_to_file resolves them correctly)
"""

import struct

# ELF constants
ELFMAG          = b'\x7fELF'
ELFCLASS64      = 2
ELFDATA2LSB     = 1
ET_DYN          = 3
EM_AARCH64      = 183
EV_CURRENT      = 1
PT_LOAD         = 1
PT_DYNAMIC      = 2
SHT_NULL        = 0
SHT_STRTAB      = 3
SHT_RELA        = 4
SHT_DYNSYM      = 11
SHT_GNU_VERNEED = 0x6ffffffe
SHT_DYNAMIC     = 6
PF_R            = 4
PF_W            = 2
PF_X            = 1
DT_NULL         = 0
DT_STRTAB       = 5
DT_STRSZ        = 10
DT_SYMTAB       = 6
DT_SYMENT       = 11
DT_JMPREL       = 23
DT_PLTRELSZ     = 2
DT_VERNEED      = 0x6ffffffe
DT_VERNEEDNUM   = 0x6fffffff
STB_GLOBAL      = 1
STT_FUNC        = 2
R_AARCH64_JUMP_SLOT = 1026

LOAD_ALIGN = 0x1000   # keep fixtures small; patcher uses this for insert_size


def _pack_ehdr(e_phoff, e_shoff, e_phnum, e_shnum, e_shstrndx):
    e_ident = ELFMAG + bytes([ELFCLASS64, ELFDATA2LSB, EV_CURRENT]) + bytes(9)
    return struct.pack('<16sHHIQQQIHHHHHH',
        e_ident, ET_DYN, EM_AARCH64, EV_CURRENT,
        0, e_phoff, e_shoff, 0,
        64, 56, e_phnum, 64, e_shnum, e_shstrndx,
    )

def _pack_phdr(p_type, p_flags, p_offset, p_vaddr, p_filesz, p_memsz, p_align):
    return struct.pack('<IIQQQQQQ',
        p_type, p_flags, p_offset, p_vaddr, p_vaddr, p_filesz, p_memsz, p_align,
    )

def _pack_shdr(sh_name, sh_type, sh_flags, sh_addr, sh_offset,
               sh_size, sh_link, sh_info, sh_addralign, sh_entsize):
    return struct.pack('<IIQQQQIIQQ',
        sh_name, sh_type, sh_flags, sh_addr, sh_offset,
        sh_size, sh_link, sh_info, sh_addralign, sh_entsize,
    )

def _pack_sym(st_name, st_info, st_shndx, st_value, st_size):
    return struct.pack('<IBBHQQ', st_name, st_info, 0, st_shndx, st_value, st_size)

def _pack_rela(r_offset, r_sym, r_type, r_addend):
    r_info = (r_sym << 32) | r_type
    return struct.pack('<QQq', r_offset, r_info, r_addend)

def _pack_dyn(d_tag, d_val):
    return struct.pack('<QQ', d_tag, d_val)

def _pack_verneed(vn_version, vn_cnt, vn_file, vn_aux, vn_next):
    return struct.pack('<HHIII', vn_version, vn_cnt, vn_file, vn_aux, vn_next)

def _pack_vernaux(vna_hash, vna_flags, vna_other, vna_name, vna_next):
    return struct.pack('<IHHII', vna_hash, vna_flags, vna_other, vna_name, vna_next)


def build_minimal_widevine_elf(extra_dynstr_bytes: int = 0,
                               extra_undef_symbol: bytes = b'') -> bytes:
    """
    Returns bytes of a minimal aarch64 ELF64 .so that widevine_patch.py
    can process without errors.

    Layout:
      [0x000] ELF header (64 bytes)
      [0x040] Program header table (3 × 56 bytes = 168 bytes)
      [0x0e8] .dynstr
      [    ?] .dynsym
      [    ?] .rela.plt
      [    ?] .gnu.version_r
      -- pad to LOAD_ALIGN boundary --  ← LOAD0 ends here
      [    ?] .dynamic  (LOAD1 starts here)
      -- pad to 8 bytes --
      [    ?] .shstrtab
      [    ?] section header table

    All data sections are in LOAD0 (p_offset=0, p_vaddr=0), so va==file_offset
    and DT_STRTAB/DT_VERNEED/DT_SYMTAB/DT_JMPREL all resolve correctly.
    .dynamic lives in LOAD1 (separate RW segment) as on the real CDM.

    extra_dynstr_bytes: append null bytes to .dynstr to test larger string tables.
    """

    # ── 1. String tables ─────────────────────────────────────────────────────

    dynstr_entries = [
        b'',                            # 0 — always empty
        b'__aarch64_ldadd4_acq_rel',    # 1
        b'__aarch64_swp4_acq_rel',      # 2
        b'libc.so.6',                   # 3
        b'GLIBC_2.17',                  # 4
        b'GLIBC_2.28',                  # 5
        b'ld-linux-aarch64.so.1',       # 6
    ]
    if extra_undef_symbol:
        dynstr_entries.append(extra_undef_symbol)
    dynstr = b'\0'.join(dynstr_entries) + b'\0'
    if extra_dynstr_bytes > 0:
        dynstr += bytes(extra_dynstr_bytes)

    ds_off = {}
    pos = 0
    for e in dynstr_entries:
        ds_off[e] = pos
        pos += len(e) + 1

    shstrtab_entries = [
        b'', b'.shstrtab', b'.dynstr', b'.dynsym',
        b'.rela.plt', b'.gnu.version_r', b'.dynamic',
    ]
    shstrtab = b'\0'.join(shstrtab_entries) + b'\0'
    ss_off = {}
    pos = 0
    for e in shstrtab_entries:
        ss_off[e] = pos
        pos += len(e) + 1

    # ── 2. .dynsym ────────────────────────────────────────────────────────────

    sym_null   = _pack_sym(0, 0, 0, 0, 0)
    sym_ldadd4 = _pack_sym(ds_off[b'__aarch64_ldadd4_acq_rel'],
                           (STB_GLOBAL << 4) | STT_FUNC, 0, 0, 0)
    sym_swp4   = _pack_sym(ds_off[b'__aarch64_swp4_acq_rel'],
                           (STB_GLOBAL << 4) | STT_FUNC, 0, 0, 0)
    dynsym = sym_null + sym_ldadd4 + sym_swp4
    if extra_undef_symbol:
        sym_extra = _pack_sym(ds_off[extra_undef_symbol],
                              (STB_GLOBAL << 4) | STT_FUNC, 0, 0, 0)
        dynsym += sym_extra   # symbol index 3

    # ── 3. .rela.plt ──────────────────────────────────────────────────────────

    # Placeholder r_offset values; the patcher doesn't use them (only rewrites type/addend)
    rela_plt = (
        _pack_rela(0x1000, 1, R_AARCH64_JUMP_SLOT, 0) +
        _pack_rela(0x1008, 2, R_AARCH64_JUMP_SLOT, 0)
    )
    if extra_undef_symbol:
        rela_plt += _pack_rela(0x1010, 3, R_AARCH64_JUMP_SLOT, 0)

    # ── 4. .gnu.version_r ────────────────────────────────────────────────────
    #
    # Two Verneed entries:
    #   libc.so.6     → GLIBC_2.17 (other=2) + GLIBC_2.28 (other=6)
    #   ld-linux…     → GLIBC_2.17 (other=3)

    vernaux_libc_217 = _pack_vernaux(0x09691a75, 0, 2, ds_off[b'GLIBC_2.17'], 16)
    vernaux_libc_228 = _pack_vernaux(0x0963cf85, 0, 6, ds_off[b'GLIBC_2.28'], 0)
    verneed_libc = _pack_verneed(1, 2, ds_off[b'libc.so.6'], 16,
                                 16 + 16 + 16)   # vn_next: past both Vernaux + next Verneed

    vernaux_ldlinux = _pack_vernaux(0x09691a75, 0, 3, ds_off[b'GLIBC_2.17'], 0)
    verneed_ldlinux = _pack_verneed(1, 1, ds_off[b'ld-linux-aarch64.so.1'], 16, 0)

    gnu_version_r = (verneed_libc + vernaux_libc_217 + vernaux_libc_228 +
                     verneed_ldlinux + vernaux_ldlinux)

    # ── 5. Layout: all read-only sections inside LOAD0 ───────────────────────

    ELF_HDR_SIZE = 64
    PHDR_SIZE    = 56
    NUM_PHDRS    = 3   # LOAD0, LOAD1, DYNAMIC

    phdr_table_off = ELF_HDR_SIZE

    cur = phdr_table_off + NUM_PHDRS * PHDR_SIZE

    def place(data, align=1):
        nonlocal cur
        cur = (cur + align - 1) & ~(align - 1)
        off = cur
        cur += len(data)
        return off

    dynstr_off    = place(dynstr)
    dynsym_off    = place(dynsym, 8)
    rela_plt_off  = place(rela_plt, 8)
    gnu_ver_r_off = place(gnu_version_r, 4)

    # LOAD0 ends at the next LOAD_ALIGN boundary — this is the insertion point
    cur = (cur + LOAD_ALIGN - 1) & ~(LOAD_ALIGN - 1)
    load0_filesz = cur

    # LOAD1 starts immediately after (dynamic section in RW segment)
    dynamic_va_offset = 0x100000   # vaddr delta for LOAD1 (va != file offset for LOAD1)
    dynamic_off  = place(b'')       # starts right at cur (= load0_filesz)

    # .dynamic entries — DT values are VAs (== file offsets because LOAD0 maps va==off)
    dynamic = (
        _pack_dyn(DT_STRTAB,    dynstr_off) +
        _pack_dyn(DT_STRSZ,     len(dynstr)) +
        _pack_dyn(DT_SYMTAB,    dynsym_off) +
        _pack_dyn(DT_SYMENT,    24) +
        _pack_dyn(DT_JMPREL,    rela_plt_off) +
        _pack_dyn(DT_PLTRELSZ,  len(rela_plt)) +
        _pack_dyn(DT_VERNEED,   gnu_ver_r_off) +
        _pack_dyn(DT_VERNEEDNUM, 2) +
        _pack_dyn(DT_NULL,      0)
    )
    dynamic_end = dynamic_off + len(dynamic)
    cur = dynamic_end

    shstrtab_off = place(shstrtab, 1)
    cur = (cur + 7) & ~7
    shdr_table_off = cur

    # ── 6. Program headers ───────────────────────────────────────────────────

    load1_off   = load0_filesz
    load1_vaddr = load0_filesz + dynamic_va_offset
    load1_size  = dynamic_end - load1_off

    phdrs = (
        _pack_phdr(PT_LOAD, PF_R | PF_X,
                   0, 0, load0_filesz, load0_filesz, LOAD_ALIGN) +
        _pack_phdr(PT_LOAD, PF_R | PF_W,
                   load1_off, load1_vaddr, load1_size, load1_size, LOAD_ALIGN) +
        _pack_phdr(PT_DYNAMIC, PF_R | PF_W,
                   dynamic_off, dynamic_off + dynamic_va_offset,
                   len(dynamic), len(dynamic), 8)
    )

    # ── 7. Section headers ───────────────────────────────────────────────────

    NUM_SHDRS = 7
    # dynsym sh_addr == dynsym_off (inside LOAD0, va==off)
    shdrs = (
        _pack_shdr(0, SHT_NULL, 0, 0, 0, 0, 0, 0, 0, 0) +
        _pack_shdr(ss_off[b'.shstrtab'], SHT_STRTAB, 0, 0,
                   shstrtab_off, len(shstrtab), 0, 0, 1, 0) +
        _pack_shdr(ss_off[b'.dynstr'], SHT_STRTAB, 0, dynstr_off,
                   dynstr_off, len(dynstr), 0, 0, 1, 0) +
        _pack_shdr(ss_off[b'.dynsym'], SHT_DYNSYM, 0, dynsym_off,
                   dynsym_off, len(dynsym), 2, 1, 8, 24) +
        _pack_shdr(ss_off[b'.rela.plt'], SHT_RELA, 0, rela_plt_off,
                   rela_plt_off, len(rela_plt), 3, 0, 8, 24) +
        _pack_shdr(ss_off[b'.gnu.version_r'], SHT_GNU_VERNEED, 0,
                   gnu_ver_r_off, gnu_ver_r_off, len(gnu_version_r), 2, 2, 4, 0) +
        _pack_shdr(ss_off[b'.dynamic'], SHT_DYNAMIC, 0,
                   dynamic_off + dynamic_va_offset, dynamic_off,
                   len(dynamic), 2, 0, 8, 16)
    )

    # ── 8. ELF header ────────────────────────────────────────────────────────

    ehdr = _pack_ehdr(
        e_phoff    = phdr_table_off,
        e_shoff    = shdr_table_off,
        e_phnum    = NUM_PHDRS,
        e_shnum    = NUM_SHDRS,
        e_shstrndx = 1,
    )

    # ── 9. Assemble ──────────────────────────────────────────────────────────

    total = shdr_table_off + len(shdrs)
    buf = bytearray(total)
    buf[0:ELF_HDR_SIZE]                                   = ehdr
    buf[phdr_table_off : phdr_table_off + len(phdrs)]     = phdrs
    buf[dynstr_off     : dynstr_off      + len(dynstr)]   = dynstr
    buf[dynsym_off     : dynsym_off      + len(dynsym)]   = dynsym
    buf[rela_plt_off   : rela_plt_off    + len(rela_plt)] = rela_plt
    buf[gnu_ver_r_off  : gnu_ver_r_off   + len(gnu_version_r)] = gnu_version_r
    buf[dynamic_off    : dynamic_off     + len(dynamic)]  = dynamic
    buf[shstrtab_off   : shstrtab_off    + len(shstrtab)] = shstrtab
    buf[shdr_table_off :]                                 = shdrs

    return bytes(buf)
