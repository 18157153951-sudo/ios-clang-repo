# ios-clang-repo

给 **roothide**（iOS 15 / 16）越狱环境补上 `clang` / `LLVM` 的 apt 源。

## 为什么需要它

roothide 的 Procursus 镜像里有 `theos-dependencies`、`ld64`、`odcctools`，但**完全没有 clang 和 LLVM**，也没有 `llvm-dev`。因为 `ld64` 和 `odcctools` 都声明依赖 `llvm-dev`，它们在这个源里同样装不上——`theos-dependencies` 于是永远无法解析。

官方 Procursus（`apt.procurs.us`）虽然有 clang，但它早已下架 iOS 16 对应的 dist，而且包路径是 `iphoneos-arm64-rootless`、架构目录也只提供 `binary-iphoneos-arm64`，设备的 apt 取不到。

本仓库把编译器这部分补齐，于是 roothide 源里的 `theos-dependencies` 就能正常安装了。

## 怎么用

在 Sileo / Zebra 里添加源：

```
https://18157153951-sudo.github.io/ios-clang-repo/
```

然后安装 `theos-dependencies`（来自 roothide 源）即可，它会连带装上本源的 `clang`、`libllvm16` 等。

也可以只装 `clang`，再自己按需装 `ld64`、`odcctools`。

## 本仓库提供什么

| 包 | 来源 | 说明 |
|---|---|---|
| `clang-16` | Procursus 重打包 | 编译器驱动本体 |
| `libllvm16` | Procursus 重打包 | LLVM 16 运行库 |
| `libclang-cpp16` | Procursus 重打包 | clang C++ 接口库 |
| `libclang-common-16-dev` | Procursus 重打包 | clang 内建头文件与 compiler-rt |
| `libc++-16-dev` | Procursus 重打包 | libc++ 头文件 |
| `clang` | Procursus 重打包 | 提供 `/usr/bin/clang -> clang-16`、`cc`、`c++`、`clang++` 等关键符号链接 |
| `libc++-dev` | Procursus 重打包 | 提供 `/usr/include/c++` 符号链接 |
| `llvm-dev` | 自建存根 | 解开 roothide 源里 `ld64` / `odcctools` 的依赖死结 |

> `clang` 和 `libc++-dev` 名字看起来像元包，体积也只有几 KB，但它们承载了 `/usr/bin/clang`、`/usr/include/c++` 这些关键符号链接，所以必须用原包重打包，不能自行造存根。

## 重打包做了什么

1. **架构改为 `iphoneos-arm64e`** —— 设备的原生架构是 arm64e，Procursus 的包声明的是 `iphoneos-arm64`，不改会被 dpkg 拒绝。二进制本身是 arm64，在 arm64e 设备上原生运行。
2. **依赖去掉版本约束** —— 可达的源不一定有 Procursus 要求的精确版本，只保留包名。
3. **压缩改为 gzip** —— 避免旧版 dpkg 不认 zstd。
4. **精简 `libclang-common-16-dev`** —— 原包安装后占 294 MB，其中 272 MB 是 compiler-rt 的 fuzzer / asan / tsan / ubsan / xray / orc 运行时，手机上编译 tweak 根本不会链接到。只保留 clang 的内建头文件（`lib/clang/16.0.0/include/`）和普通 builtins 静态库，下载从 75 MB 降到约 2 MB、安装占用从 294 MB 降到约 9 MB。

> 若之前装过完整版，可以用 `apt reinstall libclang-common-16-dev` 换成精简版以释放空间。

## 构建方式

推送到 `main` 或手动触发 `Build apt repo` workflow。runner 直接从 Procursus 拉取 deb、重打包、生成索引，然后部署到 GitHub Pages。仓库里不存放任何二进制。
