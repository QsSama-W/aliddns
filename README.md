# aliddns - 域名解析设置工具(阿里云)


## 🌟 核心功能
- 支持阿里云 AccessKey 配置，自动加载账号下所有域名
- 兼容 IPV4/IPV6 解析，可视化设置主/子域名
- 已解析记录可视化操作(启用，暂停，删除)

## 📝 使用说明
1. 从阿里云控制台创建“仅域名解析权限”的子用户，获取 AccessKey ID/Secret
2. 运行工具，填入 AccessKey 并点击“保存并加载”
3. 选择主域名、填写子域名与目标 IP，点击“设置解析”

## ⚠️ 重要安全提示
- AccessKey 会明文存储在程序目录的 AccessKey.json 中，仅在个人专属设备使用！
- 建议每 3 个月更换一次 AccessKey，降低泄露风险

## 🛠️ 使用限制说明
- 不支持 MX/CNAME 等进阶记录类型，需自行在阿里云控制台补充配置
- 仅支持 Windows 系统（无适配 Mac/Linux 的计划）

## ❓ 常见问题（FAQ）
### Q1：运行工具后提示“配置文件加载失败”怎么办？
A：检查以下两点：
1. AccessKey.json 文件是否在工具同一目录下；
2. AccessKey ID/Secret 是否填写正确（注意没有多余空格）；
3. 子用户是否已授权“AliyunDNSFullAccess”权限。

## 效果展示

<img src="https://github.com/QsSama-W/aliddns/blob/main/20251015-193408.png" style="zoom:50%;" />
