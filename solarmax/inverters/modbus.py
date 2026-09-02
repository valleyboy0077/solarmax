"""Minimal Modbus TCP client for Sigenergy / SigenStor telemetry.

This is a deliberately small, dependency-free implementation that only does
what the Solarmax adapters need: read input registers (function code 4) and
decode them into signed/unsigned integers. It is read-only by design — no
write function codes are exposed here, matching the "read-only first" safety
rule from the energy-system-modbus skill.

The register map used by the SigenStor adapter is documented in:
  ~/.hermes/skills/smart-home/energy-system-modbus/references/sigenergy-local-modbus.md
"""
from __future__ import annotations

import socket
import time
from typing import Sequence


class ModbusError(RuntimeError):
    """Raised when a Modbus request cannot be completed or is malformed."""


def read_input_registers(
    host: str,
    unit_id: int,
    address: int,
    quantity: int,
    port: int = 502,
    timeout: float = 3.0,
) -> list[int]:
    """Read a block of input registers (function code 4) over Modbus TCP.

    Returns the register values as a list of unsigned 16-bit integers in
    big-endian word order. Raises ModbusError on connection failure, timeout,
    or a malformed/exception response.

    The transaction id is derived from the address + unit + time so that
    concurrent reads do not collide, matching the pattern verified against a
    live SigenStor installation.
    """

    if quantity < 1 or quantity > 125:
        raise ModbusError(f"quantity out of range: {quantity}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))

        # Build the Modbus TCP request frame.
        tx_id = (address + unit_id + int(time.time() * 1000)) & 0xFFFF
        request = (
            tx_id.to_bytes(2, "big")          # transaction id
            + b"\x00\x00"                     # protocol id (Modbus)
            + b"\x00\x06"                     # length of the following bytes
            + bytes([unit_id])                # unit / slave id
            + b"\x04"                         # function code: read input registers
            + address.to_bytes(2, "big")      # starting register
            + quantity.to_bytes(2, "big")     # number of registers
        )

        sock.sendall(request)

        # Receive the full response. A valid read reply is at least 9 bytes:
        # tx(2) + proto(2) + length(2) + unit(1) + func(1) + byte_count(1) + data.
        data = b""
        while len(data) < 9:
            chunk = sock.recv(4096)
            if not chunk:
                raise ModbusError("connection closed before full response")
            data += chunk

        # Guard against a Modbus exception response (function code | 0x80).
        if data[7] & 0x80:
            code = data[8]
            raise ModbusError(f"Modbus exception {code} from unit {unit_id}")

        if data[7] != 0x04:
            raise ModbusError(f"unexpected function code in reply: {data[7]}")

        byte_count = data[8]
        # Wait for the full payload before decoding.
        while len(data) < 9 + byte_count:
            chunk = sock.recv(4096)
            if not chunk:
                raise ModbusError("truncated response payload")
            data += chunk

        payload = data[9 : 9 + byte_count]
        return [int.from_bytes(payload[i : i + 2], "big") for i in range(0, byte_count, 2)]
    except (OSError, socket.timeout) as exc:
        raise ModbusError(f"Modbus read failed for {host}:{port} unit={unit_id}: {exc}") from exc
    finally:
        sock.close()


def decode_s32(regs: Sequence[int]) -> int:
    """Combine two big-endian registers into a signed 32-bit integer."""

    value = (regs[0] << 16) | regs[1]
    return value if value < 0x8000_0000 else value - 0x1_0000_0000


def decode_u32(regs: Sequence[int]) -> int:
    """Combine two big-endian registers into an unsigned 32-bit integer."""

    return (regs[0] << 16) | regs[1]


def decode_u64(regs: Sequence[int]) -> int:
    """Combine four big-endian registers into an unsigned 64-bit integer."""

    return (regs[0] << 48) | (regs[1] << 32) | (regs[2] << 16) | regs[3]
