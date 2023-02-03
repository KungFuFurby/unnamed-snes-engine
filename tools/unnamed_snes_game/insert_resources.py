#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import sys
import os.path
from typing import Callable, Final, NamedTuple, Optional

from .common import MS_FS_DATA_BANK_OFFSET, ROOM_DATA_BANK_OFFSET, ResourceType, USE_RESOURCES_OVER_USB2SNES_LABEL
from .common import print_error
from .json_formats import Name, Filename, Mappings, MemoryMap
from .entity_data import ENTITY_ROM_DATA_LABEL, validate_entity_rom_data_symbols, expected_blank_entity_rom_data
from .resources_compiler import DataStore, Compilers, SharedInput, ResourceData, ResourceError
from .resources_compiler import load_shared_inputs, compile_all_resources

Address = int
RomOffset = int


def read_binary_file(path: Filename, max_size: int) -> bytes:
    with open(path, "rb") as fp:
        out = fp.read(max_size)

        if fp.read(1):
            raise RuntimeError(f"File is too large: maximum file size is { max_size }: { path }")

        return out


def get_largest_rom_address(symbols: dict[str, int]) -> int:
    # assumes max is never a zeropage or low-Ram address
    return max([a for a in symbols.values() if a & 0xFE0000 != 0x7E])


ROM_HEADER_V3_ADDR = 0xFFB0
ROM_HEADER_TITLE_ADDR = 0xFFC0
ROM_HEADER_TITLE_SIZE = 21
ROM_HEADER_TITLE_ENCODING = "Shift-JIS"  # This is supposed to be `JIS X 0201`, but python does not support it.


def convert_title(s: str) -> bytes:
    title = s.encode(ROM_HEADER_TITLE_ENCODING).ljust(ROM_HEADER_TITLE_SIZE, b"\x20")
    if len(title) != ROM_HEADER_TITLE_SIZE:
        raise ValueError(f"Title is too large ({ len(title) }, max: { ROM_HEADER_TITLE_SIZE })")
    return title


def validate_sfc_file(sfc_data: bytes, symbols: dict[str, int], mappings: Mappings) -> None:
    """
    Validates `sfc_data` matches symbols and mappings.
    """

    memory_map = mappings.memory_map
    address_to_rom_offset: Callable[[Address], RomOffset] = memory_map.mode.address_to_rom_offset

    last_symbol_bank = get_largest_rom_address(symbols) >> 16
    if last_symbol_bank >= memory_map.first_resource_bank:
        raise RuntimeError(f"ERROR: first_resource_bank is not empty.  Found a symbol in bank 0x{last_symbol_bank:02x}")

    expected_size = ((memory_map.first_resource_bank + memory_map.n_resource_banks) & 0x3F) * memory_map.mode.bank_size
    if len(sfc_data) != expected_size:
        raise RuntimeError(f"ERROR:  Expected a sfc file that is { expected_size // 1024 } bytes in size")

    # 6 spaces (unlicensed game) + 6 zeros
    # The 6 zeros is the important bit, used by the 'RomUpdateRequired' subsystem of resources-over-usb2snes.
    expected_header_start = (b" " * 6) + bytes(6)
    header_offset = address_to_rom_offset(ROM_HEADER_V3_ADDR)
    header_start_in_sfc_data = sfc_data[header_offset : header_offset + len(expected_header_start)]
    if expected_header_start != header_start_in_sfc_data:
        raise RuntimeError("ERROR: Start of header does not match expected value")

    title_offset = address_to_rom_offset(ROM_HEADER_TITLE_ADDR)

    expected_title = convert_title(mappings.game_title)
    title_in_sfc_data = sfc_data[title_offset : title_offset + ROM_HEADER_TITLE_SIZE]
    if title_in_sfc_data != expected_title:
        decoded_title_in_sfc_data = bytes(title_in_sfc_data).decode(ROM_HEADER_TITLE_ENCODING).strip()
        raise RuntimeError(
            f"ERROR: sfc file header ({ decoded_title_in_sfc_data }) does not match mappings game_title ({ mappings.game_title })"
        )

    if USE_RESOURCES_OVER_USB2SNES_LABEL in symbols:
        o = address_to_rom_offset(symbols[USE_RESOURCES_OVER_USB2SNES_LABEL])
        if sfc_data[o] != 0xFF:
            raise ValueError(f"sfc file contains resource data")


class ResourceInserter:
    BANK_END = 0x10000
    BLANK_RESOURCE_ENTRY = bytes(5)

    def __init__(self, sfc_view: memoryview, symbols: dict[str, int], mappings: Mappings):
        memory_map = mappings.memory_map

        self.view: memoryview = sfc_view
        self.symbols: dict[str, int] = symbols

        # Assume HiRom mapping
        self.address_to_rom_offset: Callable[[Address], RomOffset] = memory_map.mode.address_to_rom_offset
        self.bank_start: int = memory_map.mode.bank_start
        self.bank_size: int = memory_map.mode.bank_size

        self.bank_offset: int = memory_map.first_resource_bank
        self.n_resource_banks: int = memory_map.n_resource_banks

        self.bank_positions: list[int] = [self.bank_start] * memory_map.n_resource_banks

        validate_sfc_file(sfc_view, symbols, mappings)

    def label_offset(self, label: str) -> RomOffset:
        return self.address_to_rom_offset(self.symbols[label])

    def read_u8(self, addr: Address) -> int:
        return self.view[self.address_to_rom_offset(addr)]

    def read_u16(self, addr: Address) -> int:
        ra = self.address_to_rom_offset(addr)
        return self.view[ra] | (self.view[ra + 1] << 8)

    def subview_addr(self, addr: Address, size: int) -> memoryview:
        o = self.address_to_rom_offset(addr)
        return self.view[o : o + size]

    def insert_blob(self, blob: bytes) -> Address:
        assert isinstance(blob, bytes) or isinstance(blob, bytearray)

        blob_size = len(blob)
        assert blob_size > 0 and blob_size <= self.bank_size

        for i in range(len(self.bank_positions)):
            if self.bank_positions[i] + blob_size <= self.BANK_END:
                addr = ((self.bank_offset + i) << 16) + self.bank_positions[i]

                rom_offset = self.address_to_rom_offset(addr)

                self.view[rom_offset : rom_offset + blob_size] = blob

                self.bank_positions[i] += blob_size

                return addr

        raise RuntimeError(f"Cannot fit blob of size { blob_size } into binary")

    def insert_blob_at_label(self, label: str, blob: bytes) -> None:
        # NOTE: There is no boundary checking.  This could override data if I am not careful.
        o = self.label_offset(label)
        self.view[o : o + len(blob)] = blob

    def insert_blob_into_start_of_bank(self, bank_id: int, blob: bytes) -> Address:
        blob_size = len(blob)
        assert blob_size > 0

        u16_addr = self.bank_positions[bank_id]

        if u16_addr != self.bank_start:
            raise RuntimeError("Bank is not empty")

        if blob_size > self.BANK_END:
            raise RuntimeError("Cannot fit blob of size { blob_size } into binary")

        addr: Address = ((self.bank_offset + bank_id) << 16) + u16_addr
        rom_offset = self.address_to_rom_offset(addr)

        self.view[rom_offset : rom_offset + blob_size] = blob

        self.bank_positions[bank_id] += blob_size

        return addr

    def confirm_initial_data_is_correct(self, label: str, expected_data: bytes) -> None:
        o = self.label_offset(label)
        if self.view[o : o + len(expected_data)] != expected_data:
            raise RuntimeError(f"ROM data does not match expected data: { label }")

    def resource_table_for_type(self, resource_type: ResourceType) -> tuple[Address, int]:
        resource_type_id = resource_type.value

        nrptt_addr = self.symbols["resources.__NResourcesPerTypeTable"]
        retable_addr = self.symbols["resources.__ResourceEntryTable"]

        expected_n_resources = self.read_u8(nrptt_addr + resource_type_id)
        resource_table_addr = self.read_u16(retable_addr + resource_type_id * 2) | (retable_addr & 0xFF0000)

        return resource_table_addr, expected_n_resources

    def insert_resources(self, resource_type: ResourceType, resource_data: list[bytes]) -> None:
        table_addr, expected_n_resources = self.resource_table_for_type(resource_type)

        if len(resource_data) != expected_n_resources:
            raise RuntimeError(f"NResourcesPerTypeTable mismatch in sfc_file: { resource_type }")

        table_pos = self.address_to_rom_offset(table_addr)

        for data in resource_data:
            size = len(data)
            assert size > 0 and size < 0xFFFF

            addr = self.insert_blob(data)

            assert self.view[table_pos : table_pos + 5] == self.BLANK_RESOURCE_ENTRY

            self.view[table_pos + 0] = addr & 0xFF
            self.view[table_pos + 1] = (addr >> 8) & 0xFF
            self.view[table_pos + 2] = addr >> 16

            self.view[table_pos + 3] = size & 0xFF
            self.view[table_pos + 4] = size >> 8

            table_pos += 5

    def insert_room_data(self, bank_offset: int, rooms: list[Optional[bytes]]) -> None:
        assert len(rooms) == 256
        ROOM_TABLE_SIZE: Final = 0x100 * 2

        room_table = bytearray([0xFF]) * ROOM_TABLE_SIZE
        room_data_blob = bytearray()

        room_addr = self.bank_start + len(room_data_blob)

        for room_id, room_data in enumerate(rooms):
            if room_data:
                room_table[room_id * 2 + 0] = room_addr & 0xFF
                room_table[room_id * 2 + 1] = room_addr >> 8
                room_data_blob += room_data
                room_addr += len(room_data)

        room_table_offset = self.label_offset("resources.__RoomsTable")
        self.view[room_table_offset : room_table_offset + ROOM_TABLE_SIZE] = room_table

        self.insert_blob_into_start_of_bank(bank_offset, room_data_blob)


def insert_resources(sfc_view: memoryview, shared_input: SharedInput, data_store: DataStore) -> None:
    # sfc_view is a memoryview of a bytearray containing the SFC file

    # ::TODO confirm sfc_view is the correct file::

    n_entities: Final = len(shared_input.entities.entities)
    validate_entity_rom_data_symbols(shared_input.symbols, n_entities)

    ri = ResourceInserter(sfc_view, shared_input.symbols, shared_input.mappings)
    ri.confirm_initial_data_is_correct(ENTITY_ROM_DATA_LABEL, expected_blank_entity_rom_data(shared_input.symbols, n_entities))

    ri.insert_room_data(ROOM_DATA_BANK_OFFSET, data_store.get_data_for_all_rooms())

    msfs_entity_data = data_store.get_msfs_and_entity_data()
    assert msfs_entity_data and msfs_entity_data.msfs_data and msfs_entity_data.entity_rom_data

    ri.insert_blob_into_start_of_bank(MS_FS_DATA_BANK_OFFSET, msfs_entity_data.msfs_data)
    ri.insert_blob_at_label(ENTITY_ROM_DATA_LABEL, msfs_entity_data.entity_rom_data)

    for r_type in ResourceType:
        ri.insert_resources(r_type, data_store.get_all_data_for_type(r_type))

    # Disable resources-over-usb2snes
    if USE_RESOURCES_OVER_USB2SNES_LABEL in shared_input.symbols:
        ri.insert_blob_at_label(USE_RESOURCES_OVER_USB2SNES_LABEL, bytes(1))


def update_checksum(sfc_view: memoryview, memory_map: MemoryMap) -> None:
    """
    Update the SFC header checksum in `sfc_view` (in place).
    """

    mm_mode: Final = memory_map.mode
    cs_header_offset: Final = mm_mode.address_to_rom_offset(0x00FFDC)

    if len(sfc_view) % mm_mode.bank_size != 0:
        raise RuntimeError(f"sfc file has an invalid size (expected a multiple of { mm_mode.bank_size })")

    if len(sfc_view).bit_count() != 1:
        # ::TODO handle non-power of two ROM sizes::
        raise RuntimeError(f"Invalid sfc file size (must be a power of two in size)")

    checksum = sum(sfc_view)

    # Remove the old checksum/complement
    checksum -= sum(sfc_view[cs_header_offset : cs_header_offset + 4])

    # Add the expected `checksum + complement` value to checksum
    checksum += 0xFF + 0xFF

    checksum = checksum & 0xFFFF
    complement = checksum ^ 0xFFFF

    # Write checksum to `sfc_view`
    sfc_view[cs_header_offset + 0] = complement & 0xFF
    sfc_view[cs_header_offset + 1] = complement >> 8
    sfc_view[cs_header_offset + 2] = checksum & 0xFF
    sfc_view[cs_header_offset + 3] = checksum >> 8


class CompiledData(NamedTuple):
    shared_input: SharedInput
    data_store: DataStore


def compile_data(resources_directory: Filename, symbols_file: Filename, n_processes: Optional[int]) -> Optional[CompiledData]:
    valid = True

    def print_resource_error(re: ResourceError) -> None:
        nonlocal valid
        valid = False
        print_error(f"ERROR: { re.resource_type }[{ re.resource_id}] { re.resource_name }", re.error)

    cwd: Final = os.getcwd()
    symbols_file_relpath = os.path.relpath(symbols_file, resources_directory)

    os.chdir(resources_directory)

    shared_input: Final = load_shared_inputs(symbols_file_relpath)
    compilers: Final = Compilers(shared_input)
    data_store: Final = DataStore(shared_input.mappings)

    compile_all_resources(data_store, compilers, n_processes, print_resource_error)

    os.chdir(cwd)

    if valid:
        return CompiledData(shared_input, data_store)
    else:
        return None


def insert_resources_into_binary(resources_dir: Filename, symbols: Filename, sfc_input: Filename, n_processes: Optional[int]) -> bytes:
    co = compile_data(resources_dir, symbols, n_processes)
    if co is None:
        raise RuntimeError("Error compiling resources")

    sfc_data = bytearray(read_binary_file(sfc_input, 4 * 1024 * 1024))
    sfc_memoryview = memoryview(sfc_data)

    insert_resources(sfc_memoryview, co.shared_input, co.data_store)
    update_checksum(sfc_memoryview, co.shared_input.mappings.memory_map)

    return sfc_data
