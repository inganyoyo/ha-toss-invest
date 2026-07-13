# Brand assets (staged for home-assistant/brands)

`icon.png` / `icon@2x.png` / `logo.png` / `logo@2x.png` are staged here in the exact
layout expected by the [home-assistant/brands](https://github.com/home-assistant/brands)
repository under `custom_integrations/toss_invest/`.

Home Assistant's frontend loads integration icons from `brands.home-assistant.io`, which is
built from that repository — files placed only in this repo do **not** make the "icon not
available" placeholder go away. They must be submitted as a PR to `home-assistant/brands` and
merged there first. Once merged, remove the `ignore: brands` exception in
`.github/workflows/validate.yaml` and `release.yaml` (see the note in `README.md`).
