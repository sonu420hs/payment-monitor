[app]

title = Universal Payment Monitor
package.name = paymentmonitor
package.domain = org.example

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 0.1
requirements = python3,kivy,requests

orientation = portrait
osx.python_version = 3
osx.kivy_version = 1.9.1

fullscreen = 0

android.permissions = INTERNET
android.api = 30
android.minapi = 21
android.ndk = 23b
android.sdk = 30
android.archs = arm64-v8a, armeabi-v7a

android.allow_backup = True
android.debuggable = True

log_level = 2
warn_on_root = 0
