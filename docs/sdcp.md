# SDCP (Smart Device Control Protocol) V3.0.0 — Implementation Reference

Reference for writing a printer adapter (target device: ELEGOO Mars 5 Ultra) against the
SDCP V3.0.0 specification published by Shenzhen CBD Technology Co., Ltd.

## Source and provenance

| Item | Value |
|:--|:--|
| Specification (EN) | `https://raw.githubusercontent.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/main/SDCP%28Smart%20Device%20Control%20Protocol%29_V3.0.0_EN.md` |
| Repository | `https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0` |
| Default branch | `main` |
| Repo HEAD commit SHA | `f977215761ee2e8c6d02ab06a68dc4d2b710a255` (authored 2024-06-07T06:52:18Z) |
| Commit that last touched the EN file | `e9632aa9a967587fddbf7e1f13c1023d4ab16c95` (2024-06-07T06:50:56Z) |
| EN file git blob SHA | `08f582b5457bb5632ef8f6808c4eba6a19fe4fe8` (36,372 bytes, verified locally) |
| Date consulted | 2026-09-06 |

Complete repository contents at that commit (there are **no** attachments, examples, schemas
or sample code in the repo — only two documents):

| Type | Size | Name | Blob SHA |
|:--|--:|:--|:--|
| file | 36372 | `SDCP(Smart Device Control Protocol)_V3.0.0_EN.md` | `08f582b5457bb5632ef8f6808c4eba6a19fe4fe8` |
| file | 31764 | `SDCP(Smart Device Control Protocol)_V3.0.0_ZH.md` | `f2f1bff51f7f419f3f44c1c9740a2a7fbd166ab3` |

The Chinese document was also consulted; it is a straight translation with the same section
structure and the same content (including the same typos — see Ambiguities).

**Scope note:** The specification is generic to CBD mainboards. It never mentions ELEGOO, the
Mars series, or the Mars 5 Ultra, and it never mentions the `.goo` file format. Anything
device-specific (supported slice formats, resolution, build volume) must be read at runtime from
the attributes message; it is **not specified** in this document's source.

**Convention used below:** every statement is taken verbatim from the specification. Where the
specification is silent, the text says **not specified** explicitly. Nothing is inferred or
filled in from other sources.

---

## 1. Discovery (UDP broadcast)

The mainboard starts a WebSocket service on port **3030** after power-on.

- **Transport:** UDP
- **Destination port:** **3000**
- **Payload sent by the client:** the literal string `M99999` (6 ASCII bytes, no framing,
  no length prefix, no terminator specified)
- **Addressing:** the spec says the client broadcasts "within the local area network segment".
  The exact destination address (subnet-directed broadcast vs `255.255.255.255`) is **not specified**.

The mainboard replies with this JSON. Every field the spec defines:

```json
{
    "Id": "xxx",
    "Data": {
        "Name": "PrinterName",
        "MachineName": "MachineModel",
        "BrandName": "CBD",
        "MainboardIP": "192.168.1.2",
        "MainboardID": "000000000001d354",
        "ProtocolVersion": "V3.0.0",
        "FirmwareVersion": "V1.0.0"
    }
}
```

| Field | Type | Meaning (verbatim from spec) |
|:--|:--|:--|
| `Id` | string | Machine brand identifier, 32-bit UUID |
| `Data.Name` | string | Machine Name |
| `Data.MachineName` | string | Machine Model |
| `Data.BrandName` | string | Brand Name |
| `Data.MainboardIP` | string | Motherboard IP Address |
| `Data.MainboardID` | string | Motherboard ID (16 bit) |
| `Data.ProtocolVersion` | string | Protocol Version |
| `Data.FirmwareVersion` | string | Firmware Version |

### How the MainboardID / connection identity is obtained

`MainboardID` comes from `Data.MainboardID` of the discovery reply. It is the value that must be
substituted into every WebSocket topic string (`sdcp/request/${MainboardID}`, etc.) and echoed in
the `MainboardID` envelope field of every request. `MainboardIP` from the same reply is the host
for both the WebSocket URL and the HTTP upload URL.

The same `MainboardID` is also reported in the attributes message (`Attributes.MainboardID`), so
it can be re-confirmed after connecting.

**Not specified about discovery:**
- The source port the client should bind to, or whether the reply is unicast to the sender's
  source port or broadcast back.
- Any timeout, retry count or retry interval.
- Character encoding of the request string (ASCII/UTF-8 both give identical bytes here).
- Whether the reply is a single UDP datagram or may be fragmented across datagrams.
- Whether the reply JSON is compact or pretty-printed, and whether it has a trailing NUL/newline.
- The literal `Id` value semantics (it is called a "32-bit UUID" but the example is `"xxx"`; a
  UUID is 128 bits / 32 hex characters — see Ambiguities).
- Whether the reply's top-level shape includes a `Topic` field (the example does not have one,
  unlike every WebSocket message).
- Any authentication, pairing, or access control whatsoever. There is none in the protocol.

---

## 2. WebSocket control channel

### URL

```text
ws://${MainboardIP}:3030/websocket
```

- Scheme: `ws` (plaintext). TLS/`wss` is **not specified**.
- Host: `MainboardIP` from the discovery reply.
- Port: `3030` (the same port the HTTP file-upload server listens on).
- Path: `/websocket`.

**Not specified:** any subprotocol (`Sec-WebSocket-Protocol`), any required headers or origin,
the maximum number of concurrent client connections, reconnect/backoff behavior, or whether the
server closes idle connections.

### Heartbeat

The client sends the literal text frame:

```text
"ping"
```

and the mainboard replies:

```text
"pong"
```

The spec writes both inside quotes in a `text` block; whether the payload is the 4 bytes `ping`
or the 6 bytes `"ping"` (a JSON string) is **not specified**. The heartbeat interval, timeout,
and whether a missed `pong` should trigger reconnection are **not specified**. Use of the
RFC 6455 ping/pong control frames instead is **not specified**.

### Topics

`${MainboardID}` is the mainboard ID from discovery.

| Topic | Direction | Purpose |
|:--|:--|:--|
| `sdcp/request/${MainboardID}` | Client → Mainboard | SDCP control request |
| `sdcp/response/${MainboardID}` | Mainboard → Client | SDCP control response |
| `sdcp/status/${MainboardID}` | Mainboard → Client | Status information |
| `sdcp/attributes/${MainboardID}` | Mainboard → Client | Attribute information |
| `sdcp/error/${MainboardID}` | Mainboard → Client | Error message |
| `sdcp/notice/${MainboardID}` | Mainboard → Client | Notification message |

The topic is carried **inside the JSON message** as the `Topic` field. There is no separate
subscribe/publish mechanism; it is a plain WebSocket connection and `Topic` is how a receiver
demultiplexes message kinds. There is **no subscribe command** in the protocol — status and
attribute messages are pushed unsolicited (see §5).

### Common JSON envelope

**Request (client → mainboard).** Note the doubly-nested `Data`:

```json
{
    "Id": "xxx",
    "Data": {
        "Cmd": 0,
        "Data": {},
        "RequestID": "000000000001d354",
        "MainboardID": "ffffffff",
        "TimeStamp": 1687069655,
        "From": 0
    },
    "Topic": "sdcp/request/${MainboardID}"
}
```

| Field | Location | Meaning |
|:--|:--|:--|
| `Id` | top level | Machine brand identifier, 32-bit UUID |
| `Data.Cmd` | request body | Numeric request command (see §3) |
| `Data.Data` | request body | Command-specific payload object; `{}` when the command takes no arguments |
| `Data.RequestID` | request body | Request ID — the correlation token |
| `Data.MainboardID` | request body | Motherboard ID |
| `Data.TimeStamp` | request body | Timestamp |
| `Data.From` | request body | Source of the command (enum below) |
| `Topic` | top level | `sdcp/request/${MainboardID}` |

**Response (mainboard → client).** Identical shape minus `From`, with an `Ack` inside the inner
`Data`:

```json
{
    "Id": "xxx",
    "Data": {
        "Cmd": 0,
        "Data": {
            "Ack": 0
        },
        "RequestID": "000000000001d354",
        "MainboardID": "ffffffff",
        "TimeStamp": 1687069655
    },
    "Topic": "sdcp/response/${MainboardID}"
}
```

Unsolicited messages (status, attributes, error, notice) use a **flatter** envelope: `Id`,
a payload object (`Status` / `Attributes`, or `Data.Data` for error and notice), `MainboardID`,
`TimeStamp`, `Topic` — with **no** `Cmd`, **no** `RequestID` and **no** `From`. See §5 and §6 for
the exact shapes.

#### `From` enumeration

```text
SDCP_FROM_PC     = 0  // Local PC Software, Local Area Network
SDCP_FROM_WEB_PC = 1  // PC Software via WEB
SDCP_FROM_WEB    = 2  // Web Client
SDCP_FROM_APP    = 3  // APP
SDCP_FROM_SERVER = 4  // Server
```

A LAN adapter should send `From: 0`.

#### Request/response correlation

Correlation is by **`RequestID`**: the client puts a `RequestID` in `Data.RequestID` of the
request, and the mainboard echoes the same `RequestID` in `Data.RequestID` of the response on
`sdcp/response/${MainboardID}`. `Data.Cmd` is also echoed, so a client may match on the
(`RequestID`, `Cmd`) pair.

**Not specified about correlation:**
- The format, length or uniqueness requirement of `RequestID`. The spec's example value is
  `"000000000001d354"`, which is the same shape as a `MainboardID`, but nothing says it must be
  derived from one. A UUID string is a safe choice; nothing forbids it.
- Whether the mainboard rejects, deduplicates, or serves in parallel two in-flight requests
  carrying the same `RequestID`.
- Whether responses may arrive out of order relative to requests.
- Any per-command response timeout.
- Whether more than one response may be sent for one request.
- `TimeStamp` units (the example `1687069655` is consistent with Unix seconds; the spec does not
  state the unit or the timezone, nor whether the mainboard validates it).
- Whether the mainboard's `Id` must be echoed back by the client, or what value the client should
  send in the top-level `Id` of a request. Every example uses the placeholder `"xxx"`.

---

## 3. Command set

Every command is sent on `sdcp/request/${MainboardID}` with the envelope of §2 and a numeric
`Data.Cmd`. This is the **complete** list of commands defined by V3.0.0 — there are 16.

| Cmd | Name | Inner `Data` payload | Response `Data` |
|--:|:--|:--|:--|
| 0 | Request status refresh | `{}` | `{ "Ack": 0 }` |
| 1 | Request attribute message | `{}` | `{ "Ack": 0 }` |
| 128 | Start printing | `{ "Filename": "hitwork.ctb", "StartLayer": 0 }` | `{ "Ack": <print-ctrl ack> }` |
| 129 | Pause printing | `{}` | `{ "Ack": 0 }` |
| 130 | Stop printing | `{}` | `{ "Ack": 0 }` |
| 131 | Continue (resume) printing | `{}` | `{ "Ack": 0 }` |
| 132 | Stop feeding material | `{}` | `{ "Ack": 0 }` |
| 133 | Skip preheating | `{}` | `{ "Ack": 0 }` |
| 192 | Change printer name | `{ "Name": "newName" }` | `{ "Ack": 0 }` |
| 255 | Terminate file transfer | `{ "Uuid": "ffff…", "FileName": "xxxxx" }` | `{ "Ack": <file-transfer ack> }` |
| 258 | Retrieve file list | `{ "Url": "/usb/yourPath" }` | `{ "Ack": 0, "FileList": [...] }` |
| 259 | Batch delete files | `{ "FileList": [...], "FolderList": [...] }` | `{ "Ack": 0, "ErrData": [...] }` |
| 320 | Retrieve historical tasks | `{}` | `{ "Ack": 0, "HistoryData": [taskId, ...] }` |
| 321 | Retrieve task details | `{ "Id": ["taskId", ...] }` | `{ "Ack": 0, "HistoryDetailList": [...] }` |
| 386 | Enable/disable video stream | `{ "Enable": 0\|1 }` | `{ "Ack": <video ack>, "VideoUrl": "…" }` |
| 387 | Enable/disable time-lapse photography | `{ "Enable": 0\|1 }` | `{ "Ack": 0\|1 }` |

**There is no "upload file" WebSocket command.** File upload is an HTTP POST (§4); the only
WebSocket command in the file-transfer family is **Cmd 255, terminate file transfer**.

### 3.1 Cmd 0 — request status refresh

Payload `{}`. On receipt the mainboard re-reports the latest status to
`sdcp/status/${MainboardID}`. The response itself only carries `Ack` — the actual status arrives
as a separate status message. **The ordering of the `Ack` response versus the status push is not
specified.**

### 3.2 Cmd 1 — request attribute message

Payload `{}`. On receipt the mainboard re-reports the latest attributes to
`sdcp/attributes/${MainboardID}`. Same ordering caveat as Cmd 0.

### 3.3 Cmd 128 — start printing

```json
"Data": {
    "Filename": "hitwork.ctb",
    "StartLayer": 0
}
```

- `Filename` — "File Name or File Path". The spec does not say which form is required, nor
  whether the path prefixes of Cmd 258 (`/usb/`, `/local/`) apply here. **Not specified.**
- `StartLayer` — "Start Printing Layer Number". Whether it is 0-based or 1-based is
  **not specified**; the example uses `0` for a normal start.

Note the capitalisation: `Filename` (lowercase `n`) in Cmd 128, matching `PrintInfo.Filename`
in the status message, but `FileName` (capital `N`) in Cmd 255. Both spellings are used in the
spec and must be reproduced exactly.

**Print control Ack codes** (`sdcp_print_ctrl_ack_t`), given by the spec under Cmd 128:

| Value | Symbol | Meaning |
|--:|:--|:--|
| 0 | `SDCP_PRINT_CTRL_ACK_OK` | OK |
| 1 | `SDCP_PRINT_CTRL_ACK_BUSY` | **Busy** |
| 2 | `SDCP_PRINT_CTRL_ACK_NOT_FOUND` | **File Not Found** |
| 3 | `SDCP_PRINT_CTRL_ACK_MD5_FAILED` | **MD5 Verification Failed** |
| 4 | `SDCP_PRINT_CTRL_ACK_FILEIO_FAILED` | File Read Failed |
| 5 | `SDCP_PRINT_CTRL_ACK_INVLAID_RESOLUTION` | Resolution Mismatch |
| 6 | `SDCP_PRINT_CTRL_ACK_UNKNOW_FORMAT` | Unrecognized File Format |
| 7 | `SDCP_PRINT_CTRL_ACK_UNKNOW_MODEL` | Machine Model Mismatch |

(The symbol misspellings `INVLAID`, `UNKNOW` are verbatim from the spec.)

### 3.4 Cmd 129 / 130 / 131 — pause, stop, resume

All three take `"Data": {}` and respond with `{ "Ack": 0 }`. Mapping:

- **Pause** = Cmd **129**
- **Stop / cancel** = Cmd **130** (there is no separate "cancel" command)
- **Resume / continue** = Cmd **131**

**The spec does not attach an Ack enumeration to 129, 130 or 131** — each response example simply
shows `"Ack": 0`. Whether they can return the print-control Ack codes above (e.g. `1` Busy when
pausing an idle machine) is **not specified**. Treat any non-zero `Ack` as a failure of unknown
kind and fall back to the status stream for ground truth.

Pause and stop are asynchronous: the print sub-status passes through `PAUSING` (5) before
`PAUSED` (6), and `STOPPING` (7) before `STOPED` (8). The `Ack` only acknowledges receipt.

### 3.5 Cmd 132 — stop feeding material / Cmd 133 — skip preheating

Both take `{}` and respond `{ "Ack": 0 }`. No Ack enumeration; no preconditions stated.

### 3.6 Cmd 192 — change printer name

`"Data": { "Name": "newName" }`. Name length limits, allowed characters, and whether the change
persists across reboot are **not specified**.

### 3.7 Cmd 255 — terminate file transfer

```json
"Data": {
    "Uuid": "ffffffffffffffffffffffffffffffff",
    "FileName": "xxxxx"
}
```

`Uuid` is the "UUID for File Sending" — the same `Uuid` value used across all HTTP upload packets
(§4). This is how an in-progress upload is aborted.

**File transfer Ack codes** (`sdcp_file_transfer_ack_t`):

| Value | Symbol | Meaning |
|--:|:--|:--|
| 0 | `SDCP_FILE_TRANSFER_ACK_SUCCESS` | Success |
| 1 | `SDCP_FILE_TRANSFER_ACK_NOT_TRANSFER` | The printer is not currently transferring files |
| 2 | `SDCP_FILE_TRANSFER_ACK_CHECKING` | The printer is already in the file verification phase |
| 3 | `SDCP_FILE_TRANSFER_ACK_NOT_FOUND` | **File not found** |

Note `Ack: 2` — once the device has entered MD5 verification the transfer can no longer be
aborted.

### 3.8 Cmd 258 — retrieve file list

```json
"Data": { "Url": "/usb/yourPath" }
```

Path convention, verbatim:

- `/usb/` represents USB storage space.
- `/local/` represents onboard storage space.
- If there is no leading `/`, the default is `/local/`.

Response (the mainboard "will automatically filter out files that cannot be printed"):

```json
"Data": {
    "Ack": 0,
    "FileList": [
        {
            "name": "/usb/xxx",
            "usedSize": 123456,
            "totalSize": 123456,
            "storageType": 0,
            "type": 0
        }
    ]
}
```

| Field | Meaning |
|:--|:--|
| `name` | Current file or folder path |
| `usedSize` | Used storage space |
| `totalSize` | Total storage space |
| `storageType` | `0`: Internal Storage, `1`: External Storage |
| `type` | `0`: Folder, `1`: File |

Note the lowercase field names here — unlike every other message, which uses PascalCase. Units
for `usedSize`/`totalSize` are **not specified** (the attributes message separately gives
`RemainingMemory` in "bit"). Whether the listing recurses into subfolders, is paginated, or has a
size limit is **not specified**. There is no per-file size, mtime, or MD5 in the listing.

### 3.9 Cmd 259 — batch delete files

```json
"Data": {
    "FileList":   ["/usb/xx", "/usb/xx/xx"],
    "FolderList": ["/usb/xx", "/usb/xx/xx"]
}
```

Response:

```json
"Data": {
    "Ack": 0,
    "ErrData": ["/xxx/xxx"]
}
```

`ErrData` lists files that failed to be deleted; "if there are no failures, no return is
necessary" — i.e. **`ErrData` may be absent entirely**, so a client must not assume the key
exists. Whether `Ack` is non-zero when some deletions fail is **not specified**; a partial
failure appears to be reported as `Ack: 0` with a populated `ErrData`. Whether folder deletion is
recursive is **not specified**. Whether both `FileList` and `FolderList` are required (vs one of
them) is **not specified**.

### 3.10 Cmd 320 — retrieve historical tasks

Payload `{}`. Response `{ "Ack": 0, "HistoryData": ["taskId", ...] }` — "an ordered list of
historical records, where the array elements are the taskid (UUID) of the historical records".
The sort order (newest first or last) and the maximum number retained are **not specified**.

### 3.11 Cmd 321 — retrieve task details

```json
"Data": { "Id": ["xxxxxxxxxxx", "xxxxxxxxxxx"] }
```

Response `Data.HistoryDetailList` is an array of:

| Field | Meaning |
|:--|:--|
| `Thumbnail` | Thumbnail address |
| `TaskName` | Task name |
| `BeginTime` | Start time (Unix seconds) |
| `EndTime` | End time (Unix seconds) |
| `TaskStatus` | `0`: Other status, `1`: Completed, `2`: Exceptional status, `3`: Stopped |
| `SliceInformation` | Slice information (an object; **its contents are not specified — the spec shows `{}`**) |
| `AlreadyPrintLayer` | Printed layer count |
| `TaskId` | Task ID |
| `MD5` | MD5 of the sliced file |
| `CurrentLayerTalVolume` | Total volume of printed layers (ml) |
| `TimeLapseVideoStatus` | `0`: Not shot, `1`: File exists, `2`: Deleted, `3`: Generating, `4`: Generation failed |
| `TimeLapseVideoUrl` | URL for the time-lapse video |
| `ErrorStatusReason` | Status code — see §6.3 |

Note the inner key is `Id` (a list of task IDs), which collides in name with the top-level
envelope `Id`. The `Thumbnail` and `TimeLapseVideoUrl` URL forms are **not specified**.

### 3.12 Cmd 386 / 387 — video stream and time-lapse

See §7.

---

## 4. File upload (HTTP)

The mainboard acts as an HTTP server dedicated to file transfer.

| Item | Value |
|:--|:--|
| Endpoint | `http://${MainboardIP}:3030/uploadFile/upload` |
| Method | `POST` |
| Content-Type | `multipart/form-data` |
| Chunk size | **1 MB per packet** ("File sending is carried out using a packet-by-packet method, with each packet being 1MB") |

### Request parameters

The spec lists these verbatim (it does **not** state which are HTTP headers and which are
multipart form fields — see Ambiguities):

| Name | Example | Meaning |
|:--|:--|:--|
| `S-File-MD5` | `ffffffffffffffffffffffffffffffff` | The MD5 generated for the file, used to verify the correctness of the file |
| `Check` | `'1'` | Whether to enable file verification. `0`: disable, `1`: enable |
| `Offset` | `0` | Offset |
| `Uuid` | `xxxxxx` | The UUID for each packet is the same |
| `TotalSize` | `123` | Total size |
| `File` | `(binary)` | The chunk payload |

Notes taken directly from the text:

- `S-File-MD5` is the MD5 of the **whole file**, not of the individual chunk (it is described as
  "The MD5 generated for the file"), and the example is 32 hex characters. It is sent with
  **every** packet, as is `TotalSize` and `Uuid`.
- `Uuid` identifies the upload session: "The UUID for each packet is the same". This is the value
  passed to Cmd 255 to abort the transfer.
- `Offset` is the byte offset of this chunk within the file. Error code `-2` ("offset not match")
  implies the server tracks the expected next offset and rejects out-of-order chunks — so upload
  chunks **sequentially**, not in parallel.
- `Check` is shown quoted (`'1'`) — it is a string-valued flag.
- The units of `TotalSize` and `Offset` are **not stated** but are only coherent as bytes.

### Response

**Success:**

```json
{
    "code": "000000",
    "messages": null,
    "data": {},
    "success": true
}
```

**Failure:**

```json
{
    "code": "111111",
    "messages": [
        { "field": "common_field", "message": 100001 },
        { "field": "filename",     "message": "Cannot be empty" }
    ],
    "data": null,
    "success": false
}
```

"When the field name is set to `common_field`, the value of the `message` is the error code."
Field-level validation failures appear with the offending field name and a human-readable reason.

### Upload error codes

| Error Code | Failure Reason | Description |
|--:|:--|:--|
| -1 | offset error | Illegal file offset value (less than 0) |
| -2 | offset not match | File offset does not match the current file |
| -3 | file open failed | File cannot be opened |
| -4 | unknow error | Other unknown errors |

The example `100001` appearing in the failure sample is **not** in this table and is
**not specified** anywhere in the document.

### How completion is signaled and verified

The spec does **not** define an explicit "upload complete" response, an "is this the last chunk"
flag, or a finalisation call. What it does define:

1. **Client-side completion** is implicit: keep POSTing 1 MB chunks with increasing `Offset`
   until `Offset + len(chunk) == TotalSize`. Each chunk returns `success: true` / `code:
   "000000"` individually.
2. **Verification** is driven by the `Check` flag and `S-File-MD5`. With `Check: '1'` the
   mainboard verifies the assembled file's MD5 against `S-File-MD5`.
3. **Verification progress is observable only on the WebSocket status stream**: the machine
   top-level status is `SDCP_MACHINE_STATUS_FILE_TRANSFERRING` (2) during transfer, and the print
   sub-status becomes `SDCP_PRINT_STATUS_FILE_CHECKING` (10) during verification.
4. **Verification failure** surfaces in two independent places: `Status.PrintInfo.ErrorNumber` =
   `SDCP_PRINT_ERROR_CHECK` (1) "File MD5 Check Failed", and an error message on
   `sdcp/error/${MainboardID}` with `ErrorCode` = `SDCP_ERROR_CODE_MD5_FAILED` (1). A wrong file
   format gives `ErrorCode` = 2.
5. **Verification success has no dedicated signal.** The spec never states what message, status
   transition, or Ack indicates "file received and verified OK".

Consequences that an implementation must handle are listed in §8.

Also **not specified** for upload: the multipart part name for the file's *filename* (though the
failure example references a `filename` field), whether HTTP keep-alive/pipelining is supported,
any maximum file size, whether a partially uploaded file is retained across a disconnect and can
be resumed by re-POSTing at the last `Offset`, where the file lands (`/local/` vs `/usb/`), how
to choose the destination directory, what happens when a file of the same name already exists,
and any concurrency limit on simultaneous uploads.

---

## 5. Status and attribute reporting

### 5.1 When they are reported

**Attributes** are reported when (1) the attribute information changes, and (2) on receipt of
Cmd 1.

**Status** is reported when (1) status information changes, and (2) on receipt of Cmd 0.

There is no polling interval and no periodic push guarantee. **Not specified:** whether either
message is pushed automatically on connect.

### 5.2 Status message

```json
{
    "Status": {
        "CurrentStatus": [0,1,2,3],
        "PreviousStatus": 0,
        "PrintScreen": 0,
        "ReleaseFilm": 0,
        "TempOfUVLED": 0,
        "TimeLapseStatus": 0,
        "TempOfBox": 0,
        "TempTargetBox": 0,
        "PrintInfo": {
            "Status": 0,
            "CurrentLayer": 100,
            "TotalLayer": 1000,
            "CurrentTicks": 65535,
            "TotalTicks": 65535,
            "Filename": "HitWork.ctb",
            "ErrorNumber": 1,
            "TaskId": "xxx"
        }
    },
    "MainboardID": "ffffffff",
    "TimeStamp": 1687069655,
    "Topic": "sdcp/status/${MainboardID}"
}
```

| Field | Meaning |
|:--|:--|
| `Status.CurrentStatus` | Current machine status (see 5.3) |
| `Status.PreviousStatus` | Previous machine status |
| `Status.PrintScreen` | Total exposure screen usage time (s) |
| `Status.ReleaseFilm` | Total release film usage count |
| `Status.TempOfUVLED` | Current UVLED temperature (°C) |
| `Status.TimeLapseStatus` | Time-lapse switch status. `0`: Off, `1`: On |
| `Status.TempOfBox` | Current enclosure temperature (°C) |
| `Status.TempTargetBox` | Target enclosure temperature (°C) |
| `Status.PrintInfo.Status` | Printing sub-status (see 5.4) |
| `Status.PrintInfo.CurrentLayer` | **Current printing layer** |
| `Status.PrintInfo.TotalLayer` | **Total number of print layers** |
| `Status.PrintInfo.CurrentTicks` | **Current print time (ms)** |
| `Status.PrintInfo.TotalTicks` | **Estimated total print time (ms)** |
| `Status.PrintInfo.Filename` | Print file name |
| `Status.PrintInfo.ErrorNumber` | Print error (see 5.5) |
| `Status.PrintInfo.TaskId` | Current task ID |
| `MainboardID` | Motherboard ID |
| `TimeStamp` | Timestamp |
| `Topic` | `sdcp/status/${MainboardID}` |

**Remaining time.** There is **no explicit remaining-time field.** The only time fields are
`CurrentTicks` (elapsed, ms) and `TotalTicks` (estimated total, ms); remaining time must be
computed as `TotalTicks - CurrentTicks`. The spec does not say whether `TotalTicks` is re-estimated
during the print, whether it can shrink below `CurrentTicks`, or whether `CurrentTicks` continues
to advance while paused. Clamp the difference at zero.

**Progress.** Likewise there is no percentage field; derive it from `CurrentLayer` / `TotalLayer`.
Whether `CurrentLayer` is 0-based or 1-based is **not specified**.

### 5.3 Machine status enumeration (`CurrentStatus`, `PreviousStatus`)

```text
SDCP_MACHINE_STATUS_IDLE              = 0  // Idle
SDCP_MACHINE_STATUS_PRINTING          = 1  // Executing print task
SDCP_MACHINE_STATUS_FILE_TRANSFERRING = 2  // File transfer in progress
SDCP_MACHINE_STATUS_EXPOSURE_TESTING  = 3  // Exposure test in progress
SDCP_MACHINE_STATUS_DEVICES_TESTING   = 4  // Device self-check in progress
```

### 5.4 Print sub-status enumeration (`PrintInfo.Status`)

```text
SDCP_PRINT_STATUS_IDLE          = 0   // Idle
SDCP_PRINT_STATUS_HOMING        = 1   // Resetting
SDCP_PRINT_STATUS_DROPPING      = 2   // Descending
SDCP_PRINT_STATUS_EXPOSURING    = 3   // Exposing
SDCP_PRINT_STATUS_LIFTING       = 4   // Lifting
SDCP_PRINT_STATUS_PAUSING       = 5   // Executing Pause Action
SDCP_PRINT_STATUS_PAUSED        = 6   // Suspended
SDCP_PRINT_STATUS_STOPPING      = 7   // Executing Stop Action
SDCP_PRINT_STATUS_STOPED        = 8   // Stopped
SDCP_PRINT_STATUS_COMPLETE      = 9   // Print Completed
SDCP_PRINT_STATUS_FILE_CHECKING = 10  // File Checking in Progress
```

**Top-level vs sub-status semantics, verbatim from the spec:**

> The machine's response includes two top-level statuses: `PreviousStatus` and `CurrentStatus`,
> as well as the `Status` sub-status under `PrintInfo`.
>
> The sub-status will always retain the most recent status, for example, after the print is
> completed, the sub-status value will continue to be maintained as "Print Completed".

So `PrintInfo.Status` is **sticky**: a value of `9` (Complete) or `8` (Stopped) persists after the
job ends and does **not** by itself mean a job is running. Always gate on the top-level machine
status (e.g. `1` = Printing) before interpreting the sub-status as live.

### 5.5 `PrintInfo.ErrorNumber` enumeration

```text
SDCP_PRINT_ERROR_NONE               = 0  // Normal
SDCP_PRINT_ERROR_CHECK              = 1  // File MD5 Check Failed
SDCP_PRINT_ERROR_FILEIO             = 2  // File Read Failed
SDCP_PRINT_ERROR_INVLAID_RESOLUTION = 3  // Resolution Mismatch
SDCP_PRINT_ERROR_UNKNOWN_FORMAT     = 4  // Format Mismatch
SDCP_PRINT_ERROR_UNKNOWN_MODEL      = 5  // Machine Model Mismatch
```

These are **numerically different** from the Cmd 128 print-control Ack codes for the same
conditions (MD5 failure is Ack `3` but ErrorNumber `1`). Do not share one enum between the two.

### 5.6 Attribute message

```json
{
    "Attributes": {
        "Name": "PrinterName",
        "MachineName": "MachineModel",
        "BrandName": "CBD",
        "ProtocolVersion": "V3.0.0",
        "FirmwareVersion": "V1.0.0",
        "Resolution": "7680x4320",
        "XYZsize": "210x140x100",
        "MainboardIP": "192.168.1.1",
        "MainboardID": "000000000001d354",
        "NumberOfVideoStreamConnected": 1,
        "MaximumVideoStreamAllowed": 1,
        "NetworkStatus": "'wlan' | 'eth'",
        "UsbDiskStatus": 0,
        "Capabilities": ["FILE_TRANSFER", "PRINT_CONTROL", "VIDEO_STREAM"],
        "SupportFileType": ["CTB"],
        "DevicesStatus": {
            "TempSensorStatusOfUVLED": 0,
            "LCDStatus": 0,
            "SgStatus": 0,
            "ZMotorStatus": 0,
            "RotateMotorStatus": 0,
            "RelaseFilmState": 0,
            "XMotorStatus": 0
        },
        "ReleaseFilmMax": 0,
        "TempOfUVLEDMax": 0,
        "CameraStatus": 0,
        "RemainingMemory": 123455,
        "TLPNoCapPos": 50.0,
        "TLPStartCapPos": 30.0,
        "TLPInterLayers": 20
    },
    "MainboardID": "ffffffff",
    "TimeStamp": 1687069655,
    "Topic": "sdcp/attributes/${MainboardID}"
}
```

| Field | Meaning |
|:--|:--|
| `Name` | Machine name |
| `MachineName` | Machine model |
| `BrandName` | Brand name |
| `ProtocolVersion` | Protocol version |
| `FirmwareVersion` | Firmware version |
| `Resolution` | Resolution, `"WxH"` string |
| `XYZsize` | Maximum print dimensions in X, Y, Z, in millimeters, as a `"XxYxZ"` string |
| `MainboardIP` | Motherboard IP address |
| `MainboardID` | Motherboard ID |
| `NumberOfVideoStreamConnected` | Number of connected video streams |
| `MaximumVideoStreamAllowed` | Maximum number of video stream connections |
| `NetworkStatus` | Network connection status: `'wlan'` (WiFi) or `'eth'` (Ethernet) |
| `UsbDiskStatus` | USB drive connection. `0`: Disconnected, `1`: Connected |
| `Capabilities` | Supported sub-protocols: `FILE_TRANSFER`, `PRINT_CONTROL`, `VIDEO_STREAM` |
| `SupportFileType` | Supported slice file types, e.g. `"CTB"` |
| `DevicesStatus.TempSensorStatusOfUVLED` | `0`: Disconnected, `1`: Normal, `2`: Abnormal |
| `DevicesStatus.LCDStatus` | Exposure screen. `0`: Disconnected, `1`: Connected |
| `DevicesStatus.SgStatus` | Strain gauge. `0`: Disconnected, `1`: Normal, `2`: Calibration failed |
| `DevicesStatus.ZMotorStatus` | Z-axis motor. `0`: Disconnected, `1`: Connected |
| `DevicesStatus.RotateMotorStatus` | Rotary axis motor. `0`: Disconnected, `1`: Connected |
| `DevicesStatus.RelaseFilmState` | Release film. `0`: Abnormal, `1`: Normal (note: value polarity is inverted relative to the other DevicesStatus fields, and the key is misspelled in the spec) |
| `DevicesStatus.XMotorStatus` | X-axis motor. `0`: Disconnected, `1`: Connected |
| `ReleaseFilmMax` | Maximum number of uses (service life) for the release film |
| `TempOfUVLEDMax` | Maximum operating temperature for UVLED (°C) |
| `CameraStatus` | Camera connection. `0`: Disconnected, `1`: Connected |
| `RemainingMemory` | Remaining file storage space — the spec says the unit is **bit** |
| `TLPNoCapPos` | Model height threshold above which time-lapse is not performed (mm) |
| `TLPStartCapPos` | Print height at which time-lapse begins (mm) |
| `TLPInterLayers` | Time-lapse shooting interval, in layers |

The `Capabilities` array is the negotiation mechanism: check for `PRINT_CONTROL`,
`FILE_TRANSFER`, `VIDEO_STREAM` before using those commands. `SupportFileType` is the only
authoritative statement of which slice formats the device accepts — the `"CTB"` in the example is
just an example. **The spec does not enumerate the possible values of `SupportFileType`.**

---

## 6. Errors

There are **four distinct, non-overlapping** error/ack numbering spaces. Keep them separate:

1. Command Ack codes (per-command, §3) — three different enums.
2. `Status.PrintInfo.ErrorNumber` (§5.5).
3. `sdcp/error` `ErrorCode` (§6.1).
4. `ErrorStatusReason` in task details (§6.3).
5. Plus the HTTP upload error codes (§4), which are negative integers.

### 6.1 Error message (`sdcp/error/${MainboardID}`)

Reported proactively by the mainboard:

```json
{
    "Id": "xxx",
    "Data": {
        "Data": {
            "ErrorCode": "xxxxxx"
        },
        "MainboardID": "ffffffff",
        "TimeStamp": 1687069655
    },
    "Topic": "sdcp/error/${MainboardID}"
}
```

```text
SDCP_ERROR_CODE_MD5_FAILED    = 1,  // File Transfer MD5 Check Failed
SDCP_ERROR_CODE_FORMAT_FAILED = 2,  // File format is incorrect
```

Only these two error codes are defined. The example shows `ErrorCode` as a **string**
(`"xxxxxx"`) while the enum values are integers — parse defensively (accept both).

### 6.2 Notification message (`sdcp/notice/${MainboardID}`)

```json
{
    "Id": "xxx",
    "Data": {
        "Data": {
            "Message": "xxxxxx",
            "Type": 1
        },
        "MainboardID": "ffffffff",
        "TimeStamp": 1687069655
    },
    "Topic": "sdcp/notice/${MainboardID}"
}
```

`Message` "can be a string, can be JSON". `Type` distinguishes the notification kind; the only
defined value is `1`: History synchronization successful.

### 6.3 `ErrorStatusReason` (print-abort causes, from Cmd 321 task details)

| Value | Symbol | Meaning |
|--:|:--|:--|
| 0 | `SDCP_PRINT_CAUSE_OK` | Normal |
| 1 | `SDCP_PRINT_CAUSE_TEMP_ERROR` | Over-temperature |
| 2 | `SDCP_PRINT_CAUSE_CALIBRATE_FAILED` | Strain gauge calibration failed |
| 3 | `SDCP_PRINT_CAUSE_RESIN_LACK` | Resin level low detected |
| 4 | `SDCP_PRINT_CAUSE_RESIN_OVER` | Resin required by the model exceeds the vat's maximum capacity |
| 5 | `SDCP_PRINT_CAUSE_PROBE_FAIL` | No resin detected |
| 6 | `SDCP_PRINT_CAUSE_FOREIGN_BODY` | Foreign object detected |
| 7 | `SDCP_PRINT_CAUSE_LEVEL_FAILED` | Auto-levelling failed |
| 8 | `SDCP_PRINT_CAUSE_RELEASE_FAILED` | Model detachment detected |
| 9 | `SDCP_PRINT_CAUSE_SG_OFFLINE` | Strain gauge not connected |
| 10 | `SDCP_PRINT_CAUSE_LCD_DET_FAILED` | LCD screen connection abnormal |
| 11 | `SDCP_PRINT_CAUSE_RELEASE_OVERCOUNT` | Cumulative release film usage has reached the maximum |
| 12 | `SDCP_PRINT_CAUSE_UDISK_REMOVE` | USB drive removed; printing stopped |
| 13 | `SDCP_PRINT_CAUSE_HOME_FAILED_X` | X-axis motor anomaly; printing stopped |
| 14 | `SDCP_PRINT_CAUSE_HOME_FAILED_Z` | Z-axis motor anomaly; printing stopped |
| 15 | `SDCP_PRINT_CAUSE_RESIN_ABNORMAL_HIGH` | Resin level exceeds maximum; printing stopped |
| 16 | `SDCP_PRINT_CAUSE_RESIN_ABNORMAL_LOW` | Resin level too low; printing stopped |
| 17 | `SDCP_PRINT_CAUSE_HOME_FAILED` | Home position calibration failed (check motor / limit switch) |
| 18 | `SDCP_PRINT_CAUSE_PLAT_FAILED` | Model detected on the platform; clean it and restart printing |
| 19 | `SDCP_PRINT_CAUSE_ERROR` | Printing exception |
| 20 | `SDCP_PRINT_CAUSE_MOVE_ABNORMAL` | Motor movement abnormality |
| 21 | `SDCP_PRINT_CAUSE_AIC_MODEL_NONE` | No model detected |
| 22 | `SDCP_PRINT_CAUSE_AIC_MODEL_WARP` | Warping of the model detected |
| 23 | `SDCP_PRINT_CAUSE_HOME_FAILED_Y` | **Deprecated** |
| 24 | `SDCP_PRINT_CAUSED_FILE_ERROR` | Error file |
| 25 | `SDCP_PRINT_CAUSED_CAMERA_ERROR` | Camera error — check connection, or disable the feature to continue printing |
| 26 | `SDCP_PRINT_CAUSED_NETWORK_ERROR` | Network connection error — or disable the feature to continue printing |
| 27 | `SDCP_PRINT_CAUSED_SERVER_CONNECT_FAILED` | Server connection failed — or disable the feature to continue printing |
| 28 | `SDCP_PRINT_CAUSED_DISCONNECT_APP` | Printer not bound to an app; enable remote control for time-lapse, or disable the feature |
| 29 | `SDCP_PIRNT_CAUSED_CHECK_AUTO_RESIN_FEEDER` | Check the installation of the automatic material extraction / feeding machine |
| 30 | `SDCP_PRINT_CAUSED_CONTAINER_RESIN_LOW` | Resin in the container is running low |
| 31 | `SDCP_PRINT_CAUSED_BOTTLE_DISCONNECT` | Auto feeder not correctly installed / data cable not connected |
| 32 | `SDCP_PRINT_CAUSED_FEED_TIMEOUT` | Automatic material extraction timeout; check for a blocked resin tube |
| 33 | `SDCP_PRINT_CAUSE_TANK_TEMP_SENSOR_OFFLINE` | Resin vat temperature sensor not connected |
| 34 | `SDCP_PRINT_CAUSE_TANK_TEMP_SENSOR_ERRO` | Resin vat temperature sensor over-temperature |

(Symbol misspellings `SDCP_PIRNT_…`, `…_ERRO`, and the truncated "lease check" text at 29 are
verbatim from the spec.) These codes are documented **only** as a field of the Cmd 321 task
details response. The spec does **not** state that they appear anywhere in the live status
message, so a live print failure reason may only be retrievable after the fact via Cmd 320 → 321.

### 6.4 Quick answers: busy, file-not-found, checksum failure

| Condition | Where it appears | Value |
|:--|:--|--:|
| **Busy** | Cmd 128 response `Ack` | `1` |
| **File not found** | Cmd 128 response `Ack` | `2` |
| **File not found** | Cmd 255 response `Ack` | `3` |
| **MD5 / checksum failure** | Cmd 128 response `Ack` | `3` |
| **MD5 / checksum failure** | `Status.PrintInfo.ErrorNumber` | `1` |
| **MD5 / checksum failure** | `sdcp/error` `ErrorCode` | `1` |
| **Chunk offset mismatch** | HTTP upload response error code | `-2` |

There is **no** "busy" code for any command other than 128, and **no** busy indication in the
HTTP upload API.

---

## 7. Camera / video stream

### Enabling the stream — Cmd 386

Request:

```json
"Data": { "Enable": 0 }   // 0: Disable, 1: Enable
```

Response:

```json
"Data": {
    "Ack": 0,
    "VideoUrl": "xxxx"
}
```

- `VideoUrl` — "When opening the video stream, return the RTSP protocol address." The stream is
  RTSP ("video stream data employing the RTSP protocol for real-time communication").
- **The URL form is not specified.** The spec gives only the placeholder `"xxxx"`; it does not
  state the scheme prefix, port, path, or whether credentials are embedded. Do not construct the
  URL yourself — use whatever string `VideoUrl` contains, verbatim.
- Whether `VideoUrl` is present in the `Enable: 0` (disable) response is **not specified**.

Ack codes for Cmd 386:

| Value | Meaning |
|--:|:--|
| 0 | Success |
| 1 | Exceeded maximum simultaneous streaming limit |
| 2 | **Camera does not exist** |
| 3 | Unknown error |

### How "no camera present" is reported

Three independent signals:

1. **Attributes:** `Attributes.CameraStatus` = `0` (Disconnected); `1` = Connected. Check this
   before attempting Cmd 386.
2. **Cmd 386 response:** `Ack` = `2`, "Camera does not exist".
3. **Capabilities:** absence of `"VIDEO_STREAM"` from `Attributes.Capabilities` means the
   mainboard does not support video stream transmission at all. (The spec does not explicitly
   tie `Capabilities` to the camera's physical presence — treat it as a support flag, and
   `CameraStatus` as the presence flag.)

Stream slot accounting is via `Attributes.NumberOfVideoStreamConnected` and
`Attributes.MaximumVideoStreamAllowed` (the example device allows 1). Exceeding it yields
`Ack: 1`.

A camera fault during printing surfaces as `ErrorStatusReason` = 25.

### Time-lapse — Cmd 387

```json
"Data": { "Enable": 0 }   // 0: Disable, 1: Enable
```

Response `Ack`: `0` Success, `1` Unknown error. Related attributes: `TLPNoCapPos`,
`TLPStartCapPos`, `TLPInterLayers`; related status field `TimeLapseStatus` (0 off / 1 on);
per-task results in Cmd 321's `TimeLapseVideoStatus` / `TimeLapseVideoUrl`.

---

## 8. Ambiguities and defensive handling

Everything in this section is a place where the specification is silent or self-inconsistent.
An implementation must decide a behavior; the spec will not decide it for you.

### 8.1 `CurrentStatus` is an array in the example, `PreviousStatus` is a scalar

The status example shows `"CurrentStatus": [0,1,2,3]` alongside `"PreviousStatus": 0`, with both
documented against the same scalar enum `sdcp_machine_status_t`. The spec never explains the
array. It may mean the machine can be in several concurrent states (e.g. printing while
transferring a file), or it may be a documentation shorthand for "one of these values".

**Defensive handling:** accept both shapes. Normalize to a set; if a scalar arrives, wrap it. Do
not index `[0]` blindly and do not assume the array is non-empty or ordered.

### 8.2 Duplicate / repeated acknowledgements

The spec does not say that responses are one-per-request, does not forbid the mainboard from
sending the same `RequestID` response twice, and does not define behavior when a client reuses a
`RequestID`.

**Defensive handling:** make response handling idempotent. Key pending requests by `RequestID`,
resolve on first matching response, and **discard** later duplicates rather than treating a second
`Ack` as a second event. Use a fresh unique `RequestID` (e.g. a UUID) per request.

### 8.3 A start command that is acknowledged but does not begin

`Ack: 0` for Cmd 128 confirms only that the command was accepted. The spec defines **no** timeout
for the transition into printing, and the sub-status is sticky (§5.4) so it may still read
`COMPLETE` (9) or `STOPED` (8) from the previous job at the moment the Ack arrives. There is also
no `TaskId` in the Cmd 128 response — the new task's ID appears only later in the status stream.

**Defensive handling:** treat `Ack: 0` as "accepted", not "started". Confirm the start by watching
`sdcp/status` for `CurrentStatus` becoming `1` (Printing) **and** `PrintInfo.Status` moving into a
live value (`HOMING` 1 / `DROPPING` 2 / `EXPOSURING` 3 / `LIFTING` 4) **and**, ideally,
`PrintInfo.TaskId` changing from the previously observed value. Apply your own start timeout
(the spec provides none), and on expiry issue Cmd 0 to force a status refresh before declaring
failure. Record the pre-start `TaskId` and `PrintInfo.Status` so you can tell a stale sticky
status from a new job.

### 8.4 Disconnect between upload and start

The upload is HTTP, the start is WebSocket; they are separate connections and the spec ties them
together only through the file name. It does not say whether a file is retained if the WebSocket
drops after the last chunk, whether MD5 verification continues, where a verification result is
stored, or whether a partially uploaded file is resumable or garbage-collected.

**Defensive handling:**
- After reconnecting, do **not** assume the upload survived. Verify with Cmd 258 (retrieve file
  list) that the file exists at the expected path before sending Cmd 128.
- Persist the upload `Uuid`, the target file name, the byte `Offset` reached, and the file MD5
  across reconnects so an abort (Cmd 255) or a resume attempt is possible.
- Treat a Cmd 128 `Ack` of `2` (File Not Found) or `3` (MD5 Verification Failed) as "re-upload
  required", not as a transient error to retry.
- Consider sending Cmd 255 with the stored `Uuid` before restarting an upload, and be ready for
  `Ack: 1` ("not currently transferring") — that is a benign no-op — or `Ack: 2` ("already in the
  verification phase"), which means the abort was too late and you must wait for the verification
  result on `sdcp/status` / `sdcp/error`.

### 8.5 No positive "upload verified OK" signal

§4 covers this: failure is signaled (ErrorNumber 1, ErrorCode 1) but success is not.

**Defensive handling:** infer success by waiting for the machine status to leave
`FILE_TRANSFERRING` (2) and the sub-status to leave `FILE_CHECKING` (10) **without** an
`ErrorNumber` of 1/2 and without an `sdcp/error` message, then confirm with Cmd 258 that the file
is present. Bound the wait with your own timeout.

### 8.6 Ack enumerations are missing for most commands

Only Cmd 128 (print control), Cmd 255 (file transfer), Cmd 386 (video), and Cmd 387 (time-lapse)
have documented Ack enums. Cmds 0, 1, 129, 130, 131, 132, 133, 192, 258, 259, 320, 321 show only
`"Ack": 0` with no failure values defined.

**Defensive handling:** treat `Ack == 0` as success and any other value as an opaque failure;
log the raw value; never map an undocumented value onto the Cmd 128 enum.

### 8.7 Documentation typos to code around

- **Cmd 258's response example says `"Cmd": 192`.** This is a copy-paste error, present
  identically in both the EN and ZH documents. Do not match responses by `Cmd` alone — match on
  `RequestID` and tolerate a wrong `Cmd` echo.
- `Filename` (Cmd 128, `PrintInfo`) vs `FileName` (Cmd 255) — inconsistent capitalisation, both
  as-specified.
- Cmd 258's `FileList` entries use lowercase keys (`name`, `usedSize`, `totalSize`,
  `storageType`, `type`) while the rest of the protocol uses PascalCase.
- `RelaseFilmState` (attributes) and `SDCP_PRINT_STATUS_STOPED` are misspelled in the spec; use
  the misspelling on the wire.
- `Id` is described as a "32-bit UUID" throughout, but a UUID is 128 bits / 32 hex characters;
  `MainboardID` is described as "16 bit" while the example `000000000001d354` is 16 hex
  characters. Read these as *character counts*, not bit counts.
- Cmd 387's response example has a trailing comma after `"Ack": 0,` — invalid JSON in the doc
  only; use a tolerant or standard parser as appropriate but do not emit trailing commas.
- The Cmd 258 `Url` field is spelled `Url`, not `URL` or `Path`.

### 8.8 Field types are unstated

`Ack`, `Enable`, `Check`, `Offset`, `TotalSize`, `ErrorCode` and `Type` appear variously as
integers, quoted strings (`Check: '1'`, `ErrorCode: "xxxxxx"`), or with no example at all.

**Defensive handling:** on receive, coerce numerics tolerantly (accept `0` and `"0"`). On send,
match the spec's example literally: integers where the example shows an integer, string where it
shows a quoted value.

### 8.9 Transport-level gaps

- **No authentication, authorisation, or encryption of any kind** — anyone on the LAN who can
  reach port 3030 has full control including delete and start-print. Treat the device as
  unauthenticated infrastructure and confine it to a trusted network segment.
- No maximum WebSocket message size, no fragmentation rules, no statement about whether messages
  are sent as text or binary frames (they are JSON, so text is the reasonable choice).
- No sequence numbers or delivery guarantees on the status/attribute push streams — a status
  update can be missed. Poll with Cmd 0 / Cmd 1 after every reconnect and periodically as a
  safety net.
- No defined behavior for multiple simultaneous clients (e.g. the printer's own touchscreen
  starting a job while an adapter is connected). Assume the device state can change without your
  command and treat the status stream as the single source of truth.
- No stated behavior when a command arrives in a state where it makes no sense (pause while
  idle, resume while printing). Gate commands on the observed status yourself.

### 8.10 Storage and path semantics

- The Cmd 128 `Filename` and the Cmd 258 `/usb/` `/local/` path convention are documented
  separately and never linked. Whether Cmd 128 accepts a `/usb/...` path is **not specified**.
- The upload endpoint has no destination-path parameter at all, so where an uploaded file lands is
  **not specified**.
- `RemainingMemory` is documented in "bit" while `usedSize`/`totalSize` in the file list have no
  unit. Do not mix them in one calculation.

### 8.11 Device-specific unknowns for the Mars 5 Ultra

The spec is vendor-generic. The following must be read from the live attributes message, not
assumed: `SupportFileType` (whether `.goo`, `.ctb`, or both are accepted), `Resolution`,
`XYZsize`, `MaximumVideoStreamAllowed`, `CameraStatus`, and `Capabilities`. The spec's `"CTB"`
example is **not** a statement about any ELEGOO model. Firmware may also implement commands or
fields beyond V3.0.0; anything not in this document is undocumented behavior.

---

## Appendix: command quick reference

```text
Discovery : UDP :3000, payload "M99999"  ->  JSON with Data.MainboardID / Data.MainboardIP
WebSocket : ws://${MainboardIP}:3030/websocket
Upload    : POST http://${MainboardIP}:3030/uploadFile/upload  (multipart/form-data, 1 MB chunks)
Heartbeat : send "ping" -> receive "pong"

Cmd   0  status refresh request
Cmd   1  attributes request
Cmd 128  start printing      {Filename, StartLayer}
Cmd 129  pause printing      {}
Cmd 130  stop printing       {}
Cmd 131  continue printing   {}
Cmd 132  stop feeding material {}
Cmd 133  skip preheating     {}
Cmd 192  change printer name {Name}
Cmd 255  terminate file transfer {Uuid, FileName}
Cmd 258  retrieve file list  {Url}
Cmd 259  batch delete files  {FileList, FolderList}
Cmd 320  retrieve historical tasks {}
Cmd 321  retrieve task details {Id: [...]}
Cmd 386  enable/disable video stream {Enable}  -> {Ack, VideoUrl}
Cmd 387  enable/disable time-lapse  {Enable}
```

## Adapter behavior in voxelmill

`src/voxelmill/printer.py` implements this protocol as `SDCPPrinterAdapter`. The adapter has no
network side effects when constructed. `connect()` uses only a previously supplied
`Discovery`/target and never invokes discovery. The standalone `discover()` function is the
explicit opt-in operation that sends the UDP broadcast; applications should make this an
operator action and display the unauthenticated-LAN warning before doing it.

The adapter starts a reader for the plain WebSocket text stream and correlates responses by
`Data.RequestID`. It accepts a wrong echoed command number (the specification's Cmd 258 example
has that typo), ignores duplicate responses after the first response, and stores status,
attributes, error, and notice messages as they arrive. `status()` and `request_attributes()` send
Cmd 0 and Cmd 1 respectively so a caller can force a refresh after reconnecting. Initial
connection also requests both explicitly because SDCP does not guarantee a push on connect.

`upload()` sends sequential multipart POSTs no larger than 1 MiB. Every packet repeats the whole
file MD5, upload UUID, total size, and byte offset. The returned filename is the value accepted by
Cmd 128; the protocol does not define the upload destination path or a positive MD5-complete
signal. A caller should therefore confirm the file with Cmd 258 and watch status/error messages
before starting when that distinction matters. An interrupted upload leaves the UUID, MD5 and
last offset in the adapter's process-local upload state; it is not silently resumed after a
reconnect.

`start()` treats Ack 0 as acceptance only. By default it waits for a status message whose machine
status is Printing, whose sub-status is one of the live motion/exposure values, and whose filename
or changed task ID identifies the requested file. It refuses to start while a fresh status says
the machine is printing or transferring. If an accepted command does not meet those conditions by
the configured timeout, it records an uncertain start, sends one final status refresh, and raises
an error; a later retry first reconciles that state and will not issue a duplicate Cmd 128 while
the outcome is ambiguous. `reconcile_start()` exposes that check directly. `wait=False` is
available for an application that wants to own this reconciliation loop. Pause, resume, and
cancel map to Cmd 129, 131, and 130, and all control/upload/video/file-list operations check the
advertised capabilities. `camera()` uses Cmd 386 and returns the printer's `VideoUrl` verbatim;
it never constructs an RTSP URL. `history_ids()` and `history()` retrieve completed-task
identifiers and details with Cmd 320/321. `history()` batches detail requests in groups of at
most ten IDs (the observed firmware limit) and restores the Cmd 320 order. `information()`
combines fresh status and attributes
responses into read-only telemetry, including release-film usage and device-status fields; its
`release_film.assessment` is deliberately `telemetry_only`, since the protocol does not provide
a calibrated FEP-health measurement. `download_timelapse()` accepts a completed history record
whose `TimeLapseVideoStatus` is 1 and downloads its printer-supplied URL to an atomically
replaced local file. Relative URLs are resolved against the known printer host, while
non-HTTP(S) URLs are rejected.

`create_loopback_adapter()` returns a connected adapter and `LoopbackPrinterSimulator` for demos
and headless UI tests. It has no socket side effects. Network WebSocket timeout exceptions are
normalized, reader failure marks the adapter disconnected and wakes pending requests, and status
refreshes require a newly received status generation rather than returning a cached message.

### Run the SDCP workflow offline

Use the public loopback factory when developing an integration or demonstrating the control
flow on a machine with no printer. This example is self-contained and performs no UDP discovery,
WebSocket connection, HTTP upload, or other network operation:

```sh
.venv/bin/python - <<'PY'
from voxelmill.printer import create_loopback_adapter

adapter, simulator = create_loopback_adapter(request_timeout=0.5, start_timeout=0.5)
try:
    assert adapter.connected
    assert simulator.discovery.host == "loopback"
    print("printer:", adapter.attributes["Attributes"]["Name"])
    adapter.ping()
    print("idle:", adapter.status(refresh=False)["Status"]["CurrentStatus"])
    print("camera:", adapter.camera())

    # The simulator accepts a filename directly; no upload is needed for this demo.
    adapter.start("demo.goo")
    print("started:", simulator.filename, simulator.task_id != "")
    adapter.pause()
    adapter.resume()
    adapter.cancel()
    print("commands sent:", len(simulator.transport.sent))
finally:
    adapter.close()
PY
```

The output includes `printer: Loopback Printer`, `idle: 0`, `camera:
rtsp://loopback/demo`, and `started: demo.goo True`. The simulator returns successful command
acknowledgements and a live printing status for Cmd 128; it does not emulate layer progression,
file-transfer HTTP endpoints, or a physical print. To exercise `upload()` offline, construct an
adapter with a loopback transport and inject an `http_post` callback that records fields and
returns `{"code": "000000", "success": True}`, as `tests/test_printer.py` does. Never replace
the callback with the default HTTP implementation for an offline run.

The test suite uses `LoopbackTransport`, an in-memory WebSocket-like transport, to cover discovery
parsing, request correlation, busy acknowledgements, upload chunk ordering, start reconciliation,
camera URLs, and timeout behavior. Those tests do not open a socket. Physical Mars 5 Ultra
discovery, upload, and control remain a deferred acceptance test and must not be performed while
another job is printing.


## Read-only hardware checkpoint, 2026-09-08

`reports/plan2/printer-readonly.json` records authorized discovery and fresh
Cmd 0 status, Cmd 1 attributes and Cmd 258 file listing from the idle Mars 5
Ultra (firmware V1.4.3). All succeeded; 53 files were listed. The optional text
ping timed out. No upload, print, motion, camera or settings commands were sent.

Idle status retained the previous filename and completed layer counts.
`XYZsize` disagreed with the configured and official build envelope; its meaning
is unverified and no profile was changed. `ReleaseFilm` exceeded
`ReleaseFilmMax` while `RelaseFilmState` remained zero; these observations alone
do not establish the film's physical condition. Printing and calibration
acceptance are still pending.


## Read-only storage and file-list checkpoint, 2026-09-10

`reports/plan2/printer-monitor-2026-09-10.json` gained a `storage` section from
an authorized Cmd 258 sweep of the idle Mars 5 Ultra. No upload, motion,
settings write or print command was sent.

**Cmd 258 answers two different shapes depending on `Url`.** The root `"/"`
returns storage roots carrying `storageType`, `totalSize` and `usedSize`; any
other URL returns directory entries carrying only `name` and `type`. The
specification's field list mixes both into one table, which is why
`SDCPPrinterAdapter.storage()` reads capacity from the root listing and
`list_files()` does not attempt to. One root was reported: `/local`, 6 742 212 608
bytes total and 4 303 093 760 used (63.8%).

`type` is 0 for a root or directory and 1 for a file. `/local/` listed 53
entries — 50 `.goo` and 3 `.ctb` — and every name carried a doubled separator
after the storage prefix (`/local//name.goo`), so consumers must normalize
rather than assume a single slash.

`/usb/` returned 16 entries whose `name` was empty and whose `type` was 0, with
`UsbDiskStatus` reported as 0 in Cmd 1 attributes. With no USB disk present the
firmware answers with unnamed placeholder records instead of an empty list or an
error, so an entry count is not evidence that media is attached; filter on a
nonempty `name`.

`/local/..` was accepted rather than rejected, listing the sibling mount points
`mmcblk0p1`, `mmcblk0p2` and `mmcblk0p3`. The firmware does not sanitize `..` in
the `Url` field. This was observed read-only; nothing outside `/local/` was
opened, downloaded or modified. Treat every `Url` the application sends as
something the printer will follow literally, and treat returned paths as
untrusted when building a local destination.

Capacity numbers the firmware omits or sends unparseably stay `None` in
`storage()` rather than becoming zero: unknown free space and no free space are
different claims and must not be conflated.
