# Migrating from LabelKit

AnnoLabel was previously published as `andesprit-labelkit`. Install `annolabel` and replace the old `labelkit` command with `annolabel` in your agent prompts and scripts. Python imports now use `annolabel`, and the core facade is `AnnoLabel` in `annolabel.core.annolabel`.

Existing `.labels.json` annotations, task handles, object IDs, and COCO datasets remain compatible. Keep active task directories in place. After switching your scripts, you can remove the previous installation with `uv tool uninstall andesprit-labelkit`. Existing releases remain available under their original name.
