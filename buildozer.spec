[app]
title = BoutiqueFacile
package.name = boutiquefacile
package.domain = org.boutiquefacile
source.dir = .
source.include_exts = py,png,json,txt
version = 1.0.0
requirements = python3,kivy==2.3.1
orientation = portrait
fullscreen = 0
icon.filename = %(source.dir)s/icon.png
android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.api = 33
android.minapi = 23
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
