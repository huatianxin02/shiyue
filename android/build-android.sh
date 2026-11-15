#!/bin/bash
# Builds the phone app without Gradle.
#
# The app uses only framework classes, so the whole toolchain is aapt2 + javac + d8
# + apksigner, all of which ship with the Android SDK. That keeps the build
# reproducible on a machine with no network and no dependency cache.
set -euo pipefail
cd "$(dirname "$0")"

SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
BUILD_TOOLS="$SDK/build-tools/36.0.0"
PLATFORM="$SDK/platforms/android-36/android.jar"
JAVA_HOME="${JAVA_HOME:-/Library/Java/JavaVirtualMachines/jdk-11.jdk/Contents/Home}"
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"

VERSION="1.2.0"
VERSION_CODE=10200
MIN_SDK=26
TARGET_SDK=36
OUT="build"
APK_NAME="拾页资料库-v${VERSION}-android.apk"

for tool in "$BUILD_TOOLS/aapt2" "$BUILD_TOOLS/d8" "$BUILD_TOOLS/zipalign" "$BUILD_TOOLS/apksigner" "$PLATFORM"; do
    [ -e "$tool" ] || { echo "缺少构建工具：$tool" >&2; exit 1; }
done

rm -rf "$OUT"
mkdir -p "$OUT/compiled" "$OUT/gen" "$OUT/classes" "$OUT/dex"

echo "1/6 编译资源"
"$BUILD_TOOLS/aapt2" compile --dir res -o "$OUT/compiled/res.zip"

echo "2/6 链接清单与资源"
"$BUILD_TOOLS/aapt2" link \
    -o "$OUT/base.apk" \
    -I "$PLATFORM" \
    --manifest AndroidManifest.xml \
    -R "$OUT/compiled/res.zip" \
    --java "$OUT/gen" \
    --min-sdk-version "$MIN_SDK" \
    --target-sdk-version "$TARGET_SDK" \
    --version-code "$VERSION_CODE" \
    --version-name "$VERSION" \
    --auto-add-overlay

echo "3/6 编译 Java"
find src "$OUT/gen" -name '*.java' > "$OUT/sources.txt"
javac -encoding UTF-8 -source 8 -target 8 \
    -bootclasspath "$PLATFORM" -classpath "$PLATFORM" \
    -d "$OUT/classes" @"$OUT/sources.txt"

echo "4/6 生成 dex"
"$BUILD_TOOLS/d8" --lib "$PLATFORM" --min-api "$MIN_SDK" --output "$OUT/dex" \
    $(find "$OUT/classes" -name '*.class')

echo "5/6 打包与对齐"
cp "$OUT/base.apk" "$OUT/unsigned.apk"
(cd "$OUT" && zip -q -j unsigned.apk dex/classes.dex)
"$BUILD_TOOLS/zipalign" -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"

echo "6/6 签名"
SIGN_MODE="${1:---debug}"
if [ "$SIGN_MODE" = --debug ]; then
    # This disposable key is for development only and is never included in source releases.
    SHIYUE_ANDROID_KEYSTORE="$PWD/$OUT/debug.keystore"
    SHIYUE_ANDROID_ALIAS=androiddebugkey
    export SHIYUE_ANDROID_STORE_PASS=android SHIYUE_ANDROID_KEY_PASS=android
    keytool -genkeypair -keystore "$SHIYUE_ANDROID_KEYSTORE" -alias "$SHIYUE_ANDROID_ALIAS" \
        -keyalg RSA -keysize 2048 -validity 365 \
        -storepass:env SHIYUE_ANDROID_STORE_PASS -keypass:env SHIYUE_ANDROID_KEY_PASS \
        -dname "CN=Android Debug,O=Development,C=US" >/dev/null 2>&1
    APK_NAME="拾页资料库-v${VERSION}-android-debug.apk"
elif [ "$SIGN_MODE" = --release ]; then
    : "${SHIYUE_ANDROID_KEYSTORE:?Set a private keystore path}"
    : "${SHIYUE_ANDROID_ALIAS:?Set the signing alias}"
    : "${SHIYUE_ANDROID_STORE_PASS:?Set the store password in the environment}"
    : "${SHIYUE_ANDROID_KEY_PASS:?Set the key password in the environment}"
    export SHIYUE_ANDROID_STORE_PASS SHIYUE_ANDROID_KEY_PASS
else
    echo '用法：build-android.sh [--debug|--release]' >&2; exit 1
fi
"$BUILD_TOOLS/apksigner" sign \
    --ks "$SHIYUE_ANDROID_KEYSTORE" --ks-key-alias "$SHIYUE_ANDROID_ALIAS" \
    --ks-pass env:SHIYUE_ANDROID_STORE_PASS --key-pass env:SHIYUE_ANDROID_KEY_PASS \
    --out "$OUT/$APK_NAME" "$OUT/aligned.apk"
"$BUILD_TOOLS/apksigner" verify "$OUT/$APK_NAME"

printf '%s\n' "已构建：$OUT/$APK_NAME"

