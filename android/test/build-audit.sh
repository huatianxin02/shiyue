#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
BT="$SDK/build-tools/36.0.0"
PLATFORM="$SDK/platforms/android-36/android.jar"
export JAVA_HOME="${JAVA_HOME:-$(/usr/libexec/java_home)}"
JDK="$JAVA_HOME/bin"
OUT=build-audit
mkdir -p "$OUT/gen" "$OUT/classes" "$OUT/dex" "$OUT/res/raw"
rm -f "$OUT/res/raw/fixture.vaultpack"
python3 ../tools/android_smoke.py --fixture "$OUT/res/raw/fixture.vaultpack"
"$BT/aapt2" compile --dir "$OUT/res" -o "$OUT/resources.zip"
"$BT/aapt2" link -o "$OUT/base.apk" -I "$PLATFORM" --manifest test/audit/AndroidManifest.xml -R "$OUT/resources.zip" --java "$OUT/gen"
"$JDK/javac" -encoding UTF-8 -source 8 -target 8 -bootclasspath "$PLATFORM" -classpath "$PLATFORM" -d "$OUT/classes" src/local/shiyue/vault/{Json,Pack,Library}.java test/local/shiyue/vault/IntegrityInstrumentation.java
"$BT/d8" --lib "$PLATFORM" --min-api 26 --output "$OUT/dex" $(find "$OUT/classes" -name '*.class')
cp "$OUT/base.apk" "$OUT/unsigned.apk"
(cd "$OUT" && zip -q -j unsigned.apk dex/classes.dex)
"$BT/zipalign" -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
export SHIYUE_ANDROID_STORE_PASS=android SHIYUE_ANDROID_KEY_PASS=android
if [ ! -f "$OUT/debug.keystore" ]; then
    "$JDK/keytool" -genkeypair -keystore "$OUT/debug.keystore" -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 365 \
        -storepass:env SHIYUE_ANDROID_STORE_PASS -keypass:env SHIYUE_ANDROID_KEY_PASS -dname "CN=Android Debug,O=Development,C=US"
fi
"$BT/apksigner" sign --ks "$OUT/debug.keystore" --ks-key-alias androiddebugkey --ks-pass env:SHIYUE_ANDROID_STORE_PASS --key-pass env:SHIYUE_ANDROID_KEY_PASS --out "$OUT/audit.apk" "$OUT/aligned.apk"
"$BT/apksigner" verify "$OUT/audit.apk"
printf 'Built isolated audit APK: %s\n' "$PWD/$OUT/audit.apk"
