# Home Assistant App: Scanner

Scanner makes a scanner on the machine Home Assistant runs on a scanner for every device in the house, through the sidebar. It scans with SANE, keeps the documents as searchable PDFs in its own archive and can copy them to a network share.

## Installation

1. Add the repository (see the README) and install **Scanner**.
2. Plug the scanner into a USB port of the machine Home Assistant runs on and switch it on. (A network scanner: see [Network scanners](#network-scanners).)
3. Start the app and open **Scanner** from the sidebar.

The app looks for scanners when it starts. Plugged one in later? Press **Search** on the Scan page.

No scanner at hand? Turn on **Test scanner** in the app's configuration: a scanner that isn't there, with a feeder of ten pages.

## Which scanners work

| | |
|---|---|
| **USB, with a SANE driver** | Most flatbed and document scanners of Canon, Epson, Fujitsu, HP, Plustek, Mustek, Avision, Kodak and others. The [SANE project's list](http://www.sane-project.org/sane-supported-devices.html) says per model how well. |
| **USB, driverless** | Printers with a scanner from about 2015 on, the ones that do AirPrint or Mopria. They speak IPP over USB and need no driver. The option *Driverless USB scanners* (on by default) looks for them. |
| **On the network** | Scanners that speak eSCL (AirScan, the scanning side of AirPrint): add them under *Network scanners*. |
| **Not** | Scanners that only work with the maker's own Linux driver: many Brother models (brscan), older HP all-in-ones over USB (HPLIP), newer Epson models (Epson Scan 2). Over the network the same device often does work, as eSCL. |

A device that is both a driverless USB scanner and has a SANE driver shows twice. Both work; if the driverless one gets in the way, turn *Driverless USB scanners* off.

The scanner's own buttons are not used: start scans from the app.

## Scanning

**Scan** has the scanner's options on the left, the preview on the right and the pages scanned so far below.

- **Source, colour, resolution, paper** are up front. **More options** has everything else the scanner's driver offers, in the driver's own groups and words. A blue dot marks what you chose; the rest is the scanner's own setting. **Reset** goes back to the scanner's own.
- Options depend on each other: choose the feeder and the paper size changes, choose black-and-white and a threshold appears. The app asks the scanner after every change, which takes a second or two.
- **Preview** scans the whole glass quickly. Drag on it to choose the area; *Paper* has the standard sizes.
- **Scan** adds the page to the pages below.

For most paper: grey or colour at 300 dpi. 150 dpi is enough to read and half the size; text recognition likes 300.

### Batches

| | |
|---|---|
| **One page** | A page at a time. Scan as many as you like; they collect below. |
| **Feeder** | Every page in the document feeder, until it is empty, or the number of pages you give. Choosing the feeder as the source switches to this. |
| **Step by step** | For loose pages or a book on the glass: scans a page and waits. Put the next page down and press **Next page**; press **Stop** when you are done (or give the number of pages). |
| **Timed** | The same without pressing: scans, waits the seconds you give so you can turn the page, and scans again, until you press **Stop** or the number of pages is reached. **Next page now** skips the wait. |

**Stop** ends a batch; the pages scanned so far stay.

**Two-sided paper on a one-sided feeder:** scan all fronts, turn the whole pile over, scan again, and press **Fronts + backs**: the second half is put between the first, in the right order. (A feeder that scans both sides has *Duplex* as a source.)

### Pages

Click a page to see it at full size. Under each page: move it earlier or later (or drag it), turn it left or right, remove it. Pages stay when the app or Home Assistant restarts.

Give the document a name and tags (separated by commas) and press **Save to archive**, or **Save and download** to also get the PDF in your browser. Without a name it gets the one from Settings (*Scan 2026-11-20 15.04*).

## The archive

**Archive** shows every document. Search finds words in the name, the tags and, with text recognition, on the pages themselves. Click a tag to see only those. A document opens with its PDF, and can be renamed, tagged, downloaded, opened in a tab, copied to the share and deleted.

The documents are in the app's own storage (`/data/archive`), so they are in the app's backup in Home Assistant and move with it to another machine. The Info page shows how large the archive is.

### Text recognition

With *Recognise text* on, the PDF gets an invisible layer with the text in its place: you can search and select it in any PDF reader. The picture itself is the scan, exactly as stored. Choose the languages of your paper under **Settings** (English, Dutch, German, French, Spanish, Italian); fewer languages is faster and more exact. A page takes a few seconds on a PC and up to half a minute on a Raspberry Pi.

## A folder or network share

Under **Settings**, *Copy to a folder or network share* takes a folder in Home Assistant's `share` or `media` folder. With *Copy every saved document there* on, each new document is copied; otherwise copy a document from the Archive when you want to.

**A share on a NAS or PC (SMB or NFS)** is connected by Home Assistant, not by the app, so the app never has its password:

1. In Home Assistant: **Settings → System → Storage → Add network storage**.
2. Give it a name (say `nas`), choose usage **Share** (or **Media**), the server, the share's name and the login.
3. In the app: **Settings → Choose…** and pick `/share/nas` (or a folder in it; you can make one there).

*File name* decides where a document lands below that folder. `{name}` `{date}` `{time}` `{year}` `{month}` `{day}` `{id}` are filled in and a `/` makes folders: `{year}/{date} {name}` gives `2026/2026-11-20 Energy bill.pdf`. A file that is already there is never replaced: the copy gets ` (2)`.

When the share is not reachable the document is saved in the archive as always, and says it was not copied; copy it again from the Archive later.

## Network scanners

The app can't search the network for scanners (that needs multicast, which doesn't reach an app). Add each one in the app's configuration:

```yaml
network_scanners:
  - name: Printer upstairs
    address: 192.168.1.50
```

The address is the scanner's IP address or name; the app asks it at `http://<address>/eSCL`. For another port or path give the whole address, such as `http://192.168.1.50:8080/eSCL`. Give the scanner a fixed address in your router.

## Configuration

| Option | Default | |
|---|---|---|
| `network_scanners` | none | Scanners on the network that speak eSCL, each with a `name` and an `address` |
| `driverless_usb` | `true` | Also look for USB scanners that need no driver (IPP over USB) |
| `test_scanner` | `false` | Add SANE's test scanner, to try the app without a scanner |
| `log_level` | `info` | How much the app logs |

Everything about scanning and documents (name, picture quality, text recognition, the folder) is under **Settings** in the app itself.

## When it doesn't work

- **No scanner found.** Is it switched on and on a USB port of the machine Home Assistant runs on (not of the PC you're looking at)? Press **Search**. In a virtual machine, pass the USB device through to the Home Assistant VM. Check the model on the SANE list; a network scanner must be added in the configuration.
- **“The scanner is busy”.** Another program is scanning, or the scanner is warming up. A device that shows twice (driverless and with a driver) can't be used both ways at once.
- **“The scanner stopped answering”.** USB scanners on a hub or a long cable sometimes do this at high resolutions: plug it in directly.
- **An option doesn't stick.** The scanner refused it with the other settings; the app says so and leaves it out.
- **The scan is black or striped.** Some scanners need their lock opened (a slider under or beside the glass) or a calibration, an option under *More options*.
- **Text recognition finds nothing.** Scan at 300 dpi, in grey rather than black-and-white, and choose the right language.

The app's **Log** tab in Home Assistant has what the scanner's driver said. With `log_level: debug` it also has every question the app asked it.
