# Offline Updates

[Español](README.md) | English

Prepare Joomla updates for environments **without internet access**.

Run the script on a computer with internet access, then copy the generated folder to the isolated network, for example using a USB drive. You can use it in two ways:

1. **Upload packages manually:** use the core ZIP with Upload & Update, and extension packages with the extension installer.
2. **Let Joomla discover updates:** serve the folder over HTTP inside the isolated network and install the generated registration extension. Joomla can then find and install updates through its usual interface, without manual database changes.

Supports Joomla core, official language packs, update XML feeds, GitHub releases, and manually downloaded extension packages.

## Requirements

- Python 3.8 or later, using only the standard library.
- Windows, Linux, or macOS.
- Internet access on the computer running the script.
- An internal HTTP server if Joomla should discover updates automatically. This script prepares files; it does not start a web server.

## Usage

Copy `config.example.json` to `config.json`, then set `url_base` to the internal address from which your Joomla servers will access the generated folder.

```bash
python joomla_offline.py
python joomla_offline.py -c another-config.json
```

Each run selects packages from the configured sources, downloads them, checks published SHA hashes when available, and regenerates the update XML files, registration package, `LEEME.txt`, and `index.html`. Previous package versions are removed unless configured otherwise.

Exit codes: **0** for success, **1** for recorded package or changelog errors, and **2** for configuration loading errors or a missing `url_base`. Some unexpected errors can still interrupt processing.

The command output and generated reports currently remain in Spanish. Configuration keys and file names must be used exactly as shown below.

## Configuration

Paths are relative to `config.json`, unless absolute.

| Key | Purpose |
|---|---|
| `url_base` | Internal URL reachable by the Joomla servers, such as `http://internal-server/joomla-offline`. This address is written into XML files. Regenerate the output if it changes. |
| `carpeta_salida` | Output folder; defaults to `joomla-offline`. |
| `ramas_joomla` | Joomla major versions, such as `["6"]` or `["5", "6"]`. Core feeds are separated by major version. |
| `idiomas` | Language codes, such as `["es-ES", "gl-ES"]`. |
| `carpeta_manual` | Folder containing manually downloaded packages; defaults to `manual` and is created automatically. |
| `manual_en_actualizaciones` | Defaults to `true`: publish manual ZIP packages as available updates. Set to `false` to copy and list them only. |
| `conservar_versiones_anteriores` | Set to `true` to keep older downloaded package versions. |
| `extensiones` | Extension sources, described below. |

### XML update sources

Use the URL listed in Joomla under System → Update Sites:

```json
{
  "nombre": "Attachments",
  "tipo": "xml",
  "url": "https://raw.githubusercontent.com/jmcameron/attachments/master/pkg_attachments/update_pkg.xml"
}
```

The script filters candidate updates against the configured Joomla branches and selects the highest compatible version. It currently publishes a single selected version per detail feed; if different branches need different packages, that feed may not cover all configured branches.

### GitHub releases

For extensions without an update XML feed:

```json
{
  "nombre": "User Tools",
  "tipo": "github",
  "repo": "Danieldiazi/joomla-user-tools",
  "patron_zip": "\\.zip$"
}
```

The script uses the first asset matching `patron_zip` in the latest release. It reads the installation manifest inside the ZIP to determine `element`, `type`, `client`, and `folder`. These values can be overridden with configuration keys of the same names.

Use `targetplatform` to override the declared Joomla compatibility expression; otherwise, the configured branches are used. This declaration does not prove that an extension has been tested on those branches.

For repositories containing multiple release families, such as separate languages, filter tags with `patron_tag`:

```json
{
  "nombre": "Attachments - Spanish",
  "tipo": "github",
  "repo": "jmcameron/attachments_translations",
  "patron_tag": "^Spanish-",
  "patron_zip": "es-ES.*\\.zip$"
}
```

This selects the most recently published matching stable release with a matching asset, excluding drafts and prereleases, from the first 100 releases returned by GitHub.

If an extension has both GitHub releases and an update XML feed, prefer `"tipo": "xml"` to preserve the author's update declarations.

Set the `GITHUB_TOKEN` environment variable if authenticated GitHub API requests are needed.

### Collection feeds

A collection feed lists several extensions and links to their detail XML files. For example:

```json
{
  "nombre": "Gantry 5",
  "tipo": "coleccion",
  "url": "http://updates.gantry.org/5.0/joomla/list.xml",
  "otras_urls": ["http://updates.gantry.org/5.0/joomla/pkg_gantry5.xml"]
}
```

The script filters collection entries against `ramas_joomla`, copies the selected detail feeds and packages, and rewrites the collection links.

- `otras_urls`: optional original update sites to list in `LEEME.txt` for disabling in Joomla. These addresses are not additional download sources.
- `elementos`: optional list of internal extension identifiers to include, such as `["pkg_gantry5", "g5_helium"]`.

## Manually downloaded packages

Some extensions have no update feed and must be downloaded manually.

1. Place packages in `manual/` next to `config.json`. Subfolders are supported.
2. Run the script. Files are copied unchanged to the output's `manual/` folder and listed in the generated reports.
3. For valid Joomla ZIP packages, the script reads the internal identifier, type, and version, then publishes `manual_xml/list.xml` and detail feeds. Joomla sites with an older installed version can discover the update.
4. To install an extension that is not already installed, use System → Install → Extensions → Upload Package File.

If several ZIPs identify the same extension, only the highest version is offered. The console indicates whether a package is offered as an update, is an older version, or is available for manual installation only because its manifest cannot be read.

The manual update feed keeps the same URL, so adding or removing packages does not require reinstalling the registration extension.

Set `"manual_en_actualizaciones": false` to copy and list manual packages without offering them as updates. This may be useful for extensions containing local customizations that need review before replacement.

The copied manual folder is synchronized: files removed from the source disappear from the output on the next run.

### Release notes for manual packages

Create a UTF-8 text file next to the ZIP, with the same base name:

```text
manual/
├── template-2.1.zip
└── template-2.1.changelog.txt
```

Write the release notes in the text file. The script generates a Joomla changelog XML file and a local link using the selected ZIP manifest's identity and version.

- Subfolders are supported.
- Only notes for the highest offered version are published.
- Missing or empty notes leave the update available without a changelog.
- Notes are converted as described in [How notes are converted](#how-notes-are-converted).
- Unreadable notes are reported without blocking the package.
- Removing the notes removes the generated changelog and its link on the next run.
- No manual update XML is generated when `manual_en_actualizaciones` is `false`.

## Generated files

```text
joomla-offline/
├── LEEME.txt
├── index.html
├── core/
│   ├── list.xml
│   ├── joomla6.xml
│   └── Joomla_6.x.y-Stable-Update_Package.zip
├── idiomas/
│   ├── translationlist_6.xml
│   └── 6/
├── extensiones/
│   └── attachments/
├── manual/
├── manual_xml/
├── joomla-offline-updates.zip
├── joomla-offline-updates.xml
└── estado.json
```

ZIP packages are not modified. When a source supplies supported SHA hashes, downloaded bytes are checked against them. Otherwise, the script calculates hashes locally for the generated feed; these do not independently authenticate the publisher.

## Setup inside the isolated network

### Option A: manual installation

Open `index.html` or `LEEME.txt` to see available packages.

- **Core:** use System → Joomla Update → Upload & Update with the ZIP from `core/`.
- **Languages and extensions:** use System → Install → Extensions → Upload Package File. Installing over an older version performs an update.

### Option B: automatic update discovery

Perform the initial setup from each Joomla administration interface:

1. **Serve the generated folder over HTTP** at `url_base`. Any internal web server can serve it, for example from `/var/www/html/joomla-offline/`. The address must be reachable from each Joomla server, since that server downloads the packages.
2. **Core:** open System → Joomla Update → Options, choose the custom update channel, and enter `<url_base>/core/list.xml`. The equivalent command is `php cli/joomla.php core:update:channel custom --url=<url_base>/core/list.xml`.
3. **Languages and extensions:** install `joomla-offline-updates.zip` through Upload Package File. It appears as **Offline Updates**, a File extension, and registers the internal update feeds.
4. Under System → Update Sites, disable the original internet sites listed in `LEEME.txt` to avoid connection errors. Keep them available for later reactivation.
5. Open System → Update → Extensions and check for updates.

After copying refreshed output to the internal server, Joomla can discover the new metadata on its next check.

The registration extension can update itself through the local feed when its registered sources or visible name change. Package downloads and changelog changes alone do not require reinstalling it. If `url_base` changes, reinstall it manually so Joomla learns the new feed addresses.

The internal extension identifier and update file names retain their original `joomla_offline` / `joomla-offline` forms to preserve updates from existing installations.

**Returning to internet sources:** uninstall Offline Updates, reactivate the original update sites, and restore the default Joomla core update channel.

### How registration works

Joomla checks active update sites and matches available updates to installed extensions by their internal identity. The generated File extension registers the local feeds through its installation manifest.

- No manual database edits are required.
- Updating an extension can register its original feed again; the local feed remains registered.
- Rebuilding update sites recreates feeds from installation manifests, including the registration extension's feeds. Original internet sources may become active again and need disabling.
- Manual packages can be offered even when their original installation manifests contain no update server.

The Spanish documentation records testing on Joomla 6.2.0, including extension discovery through a local feed, updates, uninstallation of the registration extension, and core custom-channel detection. The newer changelog features still require validation in a real Joomla installation.

## Changelogs

For XML sources, including language and collection detail feeds, the script copies entries referenced by `changelogurl` that match the offered extension and version. It rewrites the URL to the internal copy and supports relative source URLs.

For `github` sources, it generates changelog XML from the release body when notes exist.

### How notes are converted

GitHub release notes (Markdown) and `.changelog.txt` files are converted to Joomla's changelog format:

- **One item per line.** Bullets (`-`, `*`, `1.`), checkboxes, bold and code markers are removed; links become `text (url)`. Blank lines and separators are ignored.
- **Categories from headings.** A recognisable Markdown heading (`## …`) groups the following lines into the matching Joomla category: *Security*, *Fixed*, *Added/New*, *Changed/Improved*, *Removed*, *Language/Translation* (Spanish headings are recognised too). Lines without a heading, or under an unrecognised one, go to *Note*.
- **Text, never HTML.** Joomla's changelog viewer outputs items as raw HTML, so the script escapes them: notes containing `<script>` are shown as text and never run in the administrator. This also applies to changelogs copied from vendors.
- **Accented characters.** Each item is written in a CDATA section. Without it, PHP's XML parser splits text at non-ASCII characters and Joomla would show "Menú corregido" as two items, "Men" and "ú corregido".

For list feeds (languages, collections such as Gantry, and manual packages) the changelog link is also set as a `changelogurl` attribute on each list entry: Joomla builds those update rows from the list attributes, not from the detail XML, so without it the button would not appear.

If a declared changelog cannot be downloaded, is invalid, or lacks the matching version, the script omits the link, records the incident, and continues processing the package. The recorded incident produces exit code 1.

### GitHub fallback for XML sources

If `changelogurl` is absent and the download points to `github.com/owner/repository/releases/download/tag/...`, the script requests that exact tag's release notes. It only publishes notes when the tag, after removing a leading `v` or `V`, matches the XML version and the release is neither a draft nor a prerelease.

The original XML still determines the package and compatibility. This fallback does not apply to Joomla core, which uses a separate processing path.

After regenerating and copying the output, check for updates in Joomla to refresh the cached metadata.

## Limitations and integrity

- **Unverified packages:** every download is compared with the SHA hash published by its source and discarded on mismatch. When a source publishes no hash (some XML feeds, older GitHub assets) there is nothing to compare against; `LEEME.txt`, `index.html` and the console summary flag it as "integridad sin verificar". Plain `http` downloads are flagged too. HTTPS certificates are always verified.

- **Core metadata:** a custom XML channel replaces Joomla's TUF metadata flow. The script reads official targets metadata over HTTPS but does not verify its TUF signatures. ZIP hashes are checked when available. Protect the generated folder and transfer media.
- **Compatibility:** the script preserves declared XML restrictions; Joomla may refuse to offer an update outside those restrictions. Its branch matching is approximate, and a feed currently retains only one selected update version.
- **Major upgrades:** core feeds are separated by major version. A major-version migration requires a manual package and the appropriate migration checks.
- **Database:** the registration mechanism uses Joomla's installer rather than manual database operations.
- **Core release notes:** changelog generation for Joomla core is not currently implemented.

Offline Updates is an independent project and is not affiliated with or endorsed by the Joomla! Project or Open Source Matters.

## Tests

The tests need no network access: they simulate every source (core, multi-branch XML feeds, collections, GitHub releases and manual packages) with local files and run the whole mirror.

```bash
python -m unittest discover -s tests -v
```

They also run on GitHub Actions for every change: Linux and Windows, Python 3.9 and 3.13, plus Python 3.8 on Windows.

## License

GNU General Public License, version 2 or later (GPL-2.0-or-later). See [LICENSE](LICENSE).
