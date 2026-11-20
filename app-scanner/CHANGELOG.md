# Changelog

## 0.1.2

**After updating:** nothing to do.

- **Save after a batch:** *Save to archive* and *Save and download* stayed greyed out after a batch ended or was stopped, until you left the Scan page and came back. They work right away now.
- **No test scanners:** the *Test scanner* option is gone, and with it the two virtual scanners in the lists. If you had it on, they disappear after the update.
- **Any number in a number field:** a number such as 200 dpi could be marked as not valid by the browser, although the scanner takes it. A number field now takes any number in the scanner's range; the scanner rounds to what it can do and the app shows what it became.

## 0.1.1

**After updating:** nothing to do.

- **Stop works:** the Stop button could not be clicked while the scanner worked, so a batch could not be stopped. It can now, at any moment; the pages scanned so far stay.
- **Step by step:** a new batch mode for the glass. It scans a page and waits until you press *Next page*, page after page, until you press *Stop* (or the number of pages you gave is reached).
- **Next page now:** in a timed batch, skips the rest of the wait.

## 0.1.0

- First version: scan with any scanner SANE knows (USB with a driver, driverless USB, eSCL on the network), with every option of the scanner, a preview to choose the area on, and batches from the feeder or on a timer from the glass. Pages collect to be looked at, turned, reordered and removed, and are saved as PDF in an archive with names, tags and text recognition (English, Dutch, German, French, Spanish, Italian), searchable by a word on the paper. Documents can be downloaded and copied, each one or the ones you pick, to a folder in Home Assistant's share or media folder, which covers the SMB and NFS shares Home Assistant mounts. Profiles keep a scanner's settings under a name.
