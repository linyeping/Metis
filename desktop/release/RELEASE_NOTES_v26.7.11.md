# Metis 26.7.11

## Latest desktop polish

- New rounded flower branding across the desktop app, system tray, Store, and Windows installer.
- Store entries now use colored connector logos and show concrete, localized tool descriptions.
- Choose what happens when closing the main window: ask, minimize to tray, or quit. The default remains minimize to tray.
- First-run provider setup now uses the production provider registry, with Universal Model API presented first.
- Right-rail workspace cards start closed, browser activity stays hidden until a real Preview URL exists, and Settings saves in place.
- Appearance themes now use visible three-color palette swatches, while About and tool settings use clearer spacing and hierarchy.

## Performance and navigation

- Provider selection no longer composites a blurred live workspace, making model-provider scrolling substantially smoother.
- Opening Settings no longer performs unnecessary Preview screenshots or keeps animated workspace backgrounds running underneath.
- Chat, Cowork, and Code switch through a lightweight draft-first path and open a clean new-conversation screen.
- Removed the unwanted gold focus rectangle from the borderless message composer.

## Automation

- Rebuilt Automation around a compact summary strip instead of oversized metric cards.
- Task creation now fits into a concise responsive row with smaller schedule, prompt, and action controls.
- Empty states and task rows use tighter spacing so more useful content remains visible.

## Installation

- Windows 10/11 64-bit.
- This is a production installer, not a development build.
- The installer contains no local API keys. Configure a model provider after installation in Settings.
