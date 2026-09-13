# Installed community model library

I assessed public GitHub and Hugging Face print-failure models on 2026-09-13. This deployment installs 13 additional distinct detectors alongside the bundled PrintGuard model. The weights live in the persistent Docker data volume, not in this source repository.

Use **Model ▾ → choose a model → Use model**, or **Settings → Models**. All 14 options loaded and classified a supplied JPEG in an isolated container on the target Docker host. Switching took approximately 1 to 14 seconds in that check. Compatibility checks confirm tensor contracts and finite scores on neutral and sample frames; they do not establish detection accuracy on a particular printer. Experimental versions remain selectable. License labels reproduce source declarations and distinguish repository terms from model metadata. “No license stated” does not mean public domain.

I added deployment-specific tuning summaries and saved slider pairs in version 2.7.0. The selector labels poor results as trial presets, even when the model loads successfully. See [tested preset controls](hardware.md#custom-models) for applying settings when switching. A public-image test cannot establish reliable performance on a printer without representative healthy and failed camera sequences.

| Model | Source and declared terms | Status |
|---|---|---|
| Forgetti Nano | [Source](https://github.com/willuhmjs/forgetti); AGPL-3.0 model metadata; MIT repository | Experimental community |
| Forgetti Small | [Source](https://github.com/willuhmjs/forgetti); AGPL-3.0 model metadata; MIT repository | Experimental community |
| Javiai YOLOv5: error and spaghetti | [Source](https://huggingface.co/Javiai/3dprintfails-yolo5vs); Apache-2.0 model card; YOLOv5 v7 GPL-3.0 architecture | Experimental community |
| Klipper community: four defects | [Source](https://github.com/CookiezRGood/klipper-print-failure-detection); No repository license stated; YOLO architecture | Experimental community |
| MobileNetV2 community v2 | [Source](https://github.com/timwalkercs/3D-Print-Failure-Detection); No license stated | Experimental community |
| MobileNetV2 community v3 | [Source](https://github.com/timwalkercs/3D-Print-Failure-Detection); No license stated | Experimental community |
| MobileNetV2 community v4 | [Source](https://github.com/timwalkercs/3D-Print-Failure-Detection); No license stated | Experimental community |
| MobileViT XXS | [Source](https://huggingface.co/Masamsa/3d-print-failure-mobilevit-xxs); Apache-2.0 model card | Experimental community |
| Obico / The Spaghetti Detective | [Source](https://github.com/TheSpaghettiDetective/obico-server); AGPL-3.0 upstream repository; weights terms not separately stated | Classic public release |
| PatchCore anomaly detector | [Source](https://huggingface.co/Masamsa/3d-print-anomaly-patchcore); No license stated | Experimental community |
| YOLO11: six defects | [Source](https://github.com/maccheneso/homeassistant-3d-fail-detection); MIT repository; Ultralytics AGPL-3.0 architecture | Experimental community |
| YOLO11: spaghetti, stringing, zits | [Source](https://huggingface.co/ApatheticWithoutTheA/3D-Print-Failure-Detector); MIT model card; Ultralytics AGPL-3.0 architecture | Experimental community |
| YOLOv8 fault detector: best / last | [Source](https://github.com/Abhi3886/3D-Printing_Failure_Detection); No repository license stated; Ultralytics AGPL-3.0 architecture | Experimental community |

## Search limits and excluded duplicates

- Obico next-generation AI: its former private beta became an [AI Premium cloud release](https://www.obico.io/blog/next-gen-ai-failure-detection-general-release/). I found no downloadable weights in the reviewed public sources. The installed classic model is a different release.
- The Laikulo and shane806 Hugging Face Obico exports mirror the classic weights, so I keep one official-source copy.
- ApatheticWithoutTheA and dhossain-ai publish byte-identical three-defect YOLO11 checkpoints. They share one selector entry.
- MobileNet `model2lite.tflite` and `v2lite.tflite` are byte-identical. I keep v2, v3 and v4.
- Abhi3886 best and last checkpoints differ as files but contain identical learned weights. They share one selector entry.
- [Yodazon AlexNet](https://huggingface.co/Yodazon/3DPrintFailureType) publishes a state dictionary but not the complete custom network definition. I downloaded it for inspection but did not invent its missing architecture or install an unverified reconstruction.
- anlakb01 YOLO-CBAM and the reviewed SebTC, adam-steven, ciprian-stingu, LeoKenny, Wartronick and fshen6 projects did not provide retrievable compatible model weights in the inspected files. A generic COCO YOLO checkpoint was excluded because it does not detect print failures.
- This is a bounded public-source search, not a claim to have found every model on the internet. No private beta enrollment, paid service activation or access-control bypass was performed.

## Integrity and preprocessing

Each installed directory contains `model.json` and `info.json` with its source, terms and file SHA-256. PyTorch conversions used restricted `weights_only=True` loading and official neural-network classes; only exported ONNX files are loaded by PrintGuard.

- MobileViT uses the config’s class order (normal, failure), BGR channels and a center crop. Its card also contains a conflicting label table, disclosed in the selector.
- PatchCore includes normalization and score calibration in the ONNX graph. PrintGuard uses its already-normalized output, whose original decision boundary is 0.5, without applying the raw training threshold twice. Monitor presets can select a different threshold after evaluation.
- MobileNet versions include pixel rescaling in the graph and produce a normal probability; the profile maps its complement to failure.
- YOLO profiles use their actual class order. Normal objects such as the extruder and printed part do not count as failure classes.

| Bundle | SHA-256 |
|---|---|
| `forgetti-nano` | `47597b08de7613aa8230df81fdf65a41c8b7446c30ae239fa5109be695f36b82` |
| `forgetti-small` | `a474732442c06abed574f52ce8d2a645d9ea408dfde8d9cd61135291b74df786` |
| `javiai-yolov5` | `99dc0aa40c6b5f1130c31cad8868b40596307a9a95d1a9fb483b015ca899574f` |
| `klipper-community` | `8e693cd0d21cc147d9e4b1f8b7d3159b8c9c5f1a1fe9692f820f0bcef3e7101b` |
| `mobilenet-v2` | `41f1658f85a98e89928f8fb87fb070d80b602a85e7bb7b401c0ee38f315f3ebb` |
| `mobilenet-v3` | `491992de3ae7883cb67b94bb049f8890363bd9c02f6d9803b6ede6edd8fc1318` |
| `mobilenet-v4` | `d5485c325b6d5922f9aea3b54c92d000e6c5fb2150a3a350647c44ee23eac8f0` |
| `mobilevit-xxs` | `6416a94903810842313614260ba6d6800edb284e4c080970cc395b917891debb` |
| `obico-classic` | `0a6ebd8e30dbf6a450c50f9c0a5406f04ba7eb1c99fd5996e888c78bb383b9aa` |
| `patchcore` | `9a1c3c8f389244eb21cc50d51f71feb29b270d25d5445bedf57078c2a9ccbba0` |
| `yolo11-six-defects` | `0d0168f79b826143fdb83a350c802ec387d7ada0a7da0122ec30d4e6f95f18c2` |
| `yolo11-three-defects` | `3026e63e4738926622af39b796dc70ed06ebc619e6c853cfb949a5fa407a0757` |
| `yolo8-best` | `0f6191d3d836aa7222f5b7715a472df6c8a6967ad01c8b0c9ed1dc18f689483d` |
