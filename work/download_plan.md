# T1 下载计划(work/download_plan.md)

> 生成:2026-09-23 夜班。数据源:`work/space_videos.json`(最新 300 个视频,总库 2113 个,风控限制只拉到第 6 页)。
> 分类依据:标题启发式 + `is_charging_arc` 字段(空间 API 自带,准确)。

## 分类结果

| 类 | 数量 | 说明 |
|---|---|---|
| 充电专属 | 39 | 剥壳/三国三味/圆脸X系列为主,采访对话类是主力 → **目标视频** |
| 公开-中文日常 | ~260 | 单人口播评论 → **参考音来源** |
| 公开-采访类 | 少量 | 如"圆脸X小外" BV17UPszhE6o(39:58) → 冒烟用 |

充电专属全清单见 `work/classify_dump.md`(39 个,BV+标题+时长)。

## 下载顺序执行情况(任务书 ①→⑤)

| # | BV | 分类 | 状态 | 说明 |
|---|---|---|---|---|
| ① | BV1ZTr5BuEGy | 中文日常 18:19 | ✅ 122MB | 参考音源 A(产出 refs 01) |
| ① | BV1eQ5t6GEtX | 中文日常 19:55 | ✅ 107MB | 参考音源 B(产出 refs 02) |
| ① | BV1NR596KEk4 | 中文日常 25:07 | ✅ 148MB | 参考音备选(BGM 偏多未用) |
| ② | BV17UPszhE6o | 采访(公开) 39:58 | ✅ 173MB | 冒烟主选(圆脸X小外,外语原声+中字) |
| ② | BV1UdFAzrE1Z | 素材(公开) 13:27 | ✅ 130MB | 冒烟备选 |
| ③ | BV1SGem6uEU9 | 充电专属 94:17 | ⚠️ 只有 300s 试看 | 匿名只能拿 5 分钟试看片段 |
| ③ | 其余 38 个专属 | - | ❌ 未下 | **阻塞:无 SESSDATA**(见下) |
| ④⑤ | 其余采访/全量 | - | ⏸️ 未开始 | 时间与权限不允许,后续夜间继续 |

## 卡点:充电专属下载(唯一阻塞项)

- 根因:Edge 153 cookie 全部 **v20 app-bound 加密**,用户态无解;需要 SESSDATA
- 已试 3 招(上限):yt-dlp 直读 DPAPI 失败 → junction+CDP 让 Edge 自解(profile 换路径后 app-bound 密钥失效,Edge 当成新 profile)→ 无管理员权限不能 SYSTEM 解密
- 解法(早晨 10 秒):Edge 装 "Get cookies.txt LOCALLY" 扩展 → 访问 bilibili.com → 导出 cookies.txt → 覆盖 `work/cookies_full.txt`。下载脚本备用:`work/tools/download_batch.py` 改 cookie 参数即可
- 另:BBDown 1.6.3 已归档,无 getupvids 命令,且登录要扫码(禁止),不适用

## 全库清单获取

- `work/space_videos.json`:300/2113(风控 -352,页面级重试 3 次后放弃,已留 20/40/60s 退避逻辑)
- 需要全量时:跑 `work/tools/bili_space_list.py`(幂等,从第 7 页续拉需改脚本起始页),或早晨带登录 cookie 重跑更稳
