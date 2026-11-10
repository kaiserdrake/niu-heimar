# Changelog

## [0.22.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.21.0...niu-heimar-v0.22.0) (2026-11-10)


### Features

* **niu-dash:** Research tab companion, explore and day schedule; Topics view for 300 topics ([#73](https://github.com/kaiserdrake/niu-heimar/issues/73)) ([9f53cca](https://github.com/kaiserdrake/niu-heimar/commit/9f53cca5b118eb7e33bac1665c5b558557024b4b))

## [0.21.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.20.0...niu-heimar-v0.21.0) (2026-11-10)


### Features

* **ollama:** gateway auto-routing aliases and served-model reporting ([#70](https://github.com/kaiserdrake/niu-heimar/issues/70)) ([6124a8a](https://github.com/kaiserdrake/niu-heimar/commit/6124a8a3902448b611ea22645f1d926820584993))

## [0.20.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.19.0...niu-heimar-v0.20.0) (2026-11-10)


### Features

* **dashboard:** LLMs usage panel with local Ollama token usage ([#68](https://github.com/kaiserdrake/niu-heimar/issues/68)) ([008fc5d](https://github.com/kaiserdrake/niu-heimar/commit/008fc5d0655de9c538e47cf796ec550183459e28))

## [0.19.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.18.0...niu-heimar-v0.19.0) (2026-11-10)


### Features

* **dashboard:** merge Weekly and Token usage into one collapsible panel ([#66](https://github.com/kaiserdrake/niu-heimar/issues/66)) ([bc44766](https://github.com/kaiserdrake/niu-heimar/commit/bc447666c668d70cf6a8cc7742aab21157267e6b))

## [0.18.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.17.1...niu-heimar-v0.18.0) (2026-11-10)


### Features

* **dashboard:** say why usage readings stopped and renew an expired token ([#63](https://github.com/kaiserdrake/niu-heimar/issues/63)) ([6948f4e](https://github.com/kaiserdrake/niu-heimar/commit/6948f4e816845678e498111881fc22598b9c3c53))
* **dashboard:** tidy weekly usage labels, fit to data, drag-to-zoom ([#65](https://github.com/kaiserdrake/niu-heimar/issues/65)) ([247a63e](https://github.com/kaiserdrake/niu-heimar/commit/247a63eb53bd3b6e5d22229161b63877c2e2ae2e))

## [0.17.1](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.17.0...niu-heimar-v0.17.1) (2026-11-09)


### Bug Fixes

* **niu-local:** never follow HTTP redirects on requests that carry the gateway key ([#60](https://github.com/kaiserdrake/niu-heimar/issues/60)) ([949b324](https://github.com/kaiserdrake/niu-heimar/commit/949b3248a3930b2f64cfaa62fbdc2ca376ad1a06))

## [0.17.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.16.0...niu-heimar-v0.17.0) (2026-11-09)


### Features

* **niu-local:** support Ollama endpoints behind an authenticating gateway ([#58](https://github.com/kaiserdrake/niu-heimar/issues/58)) ([b876cf4](https://github.com/kaiserdrake/niu-heimar/commit/b876cf4d9b590b56d59387fd31d2e288f5f63e89))

## [0.16.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.15.1...niu-heimar-v0.16.0) (2026-11-09)


### Features

* **dashboard:** yellow Events-tab dot while PR checks run; right-align header counters ([#56](https://github.com/kaiserdrake/niu-heimar/issues/56)) ([2865d9e](https://github.com/kaiserdrake/niu-heimar/commit/2865d9ecba2c22231affb29ddabb4781c8a0d986))

## [0.15.1](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.15.0...niu-heimar-v0.15.1) (2026-11-09)


### Bug Fixes

* **dashboard:** drop the zoom hint text and reset button from Agent interactions ([#54](https://github.com/kaiserdrake/niu-heimar/issues/54)) ([45d70a5](https://github.com/kaiserdrake/niu-heimar/commit/45d70a546ec82a15d25b75ede6f701b70b7ad4bd))

## [0.15.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.14.0...niu-heimar-v0.15.0) (2026-11-09)


### Features

* **dashboard:** Token usage panel and a transcript token scanner (B2) ([#50](https://github.com/kaiserdrake/niu-heimar/issues/50)) ([0f749e3](https://github.com/kaiserdrake/niu-heimar/commit/0f749e3b58c31fb7994d653b86450741c1c55e52))


### Bug Fixes

* **dashboard:** 1-hour default, graph wheel zoom, 10% weekly floor; security-reviewer capped at Sonnet ([#52](https://github.com/kaiserdrake/niu-heimar/issues/52)) ([90c773d](https://github.com/kaiserdrake/niu-heimar/commit/90c773de98aff1a2e67ff619565b2ce364faf598))
* **dashboard:** drag to pan the Agent interactions graph; themed zoom hint ([#53](https://github.com/kaiserdrake/niu-heimar/issues/53)) ([c7cc0ee](https://github.com/kaiserdrake/niu-heimar/commit/c7cc0ee55104470f9e4ba4063bf4479f850f42d5))
* **dashboard:** say 'usage not readable' for conservative gate runs instead of 'quota is tight' ([#49](https://github.com/kaiserdrake/niu-heimar/issues/49)) ([93eca6c](https://github.com/kaiserdrake/niu-heimar/commit/93eca6cb158dc325fabc4a51e324c44fa3ec79b6))

## [0.14.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.13.0...niu-heimar-v0.14.0) (2026-11-08)


### Features

* **dashboard:** Weekly usage graph in the Events tab with a background Claude usage recorder (B1) ([#47](https://github.com/kaiserdrake/niu-heimar/issues/47)) ([673e5d6](https://github.com/kaiserdrake/niu-heimar/commit/673e5d683e8d744142bdc9375e4d51788c6eaeaf))

## [0.13.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.12.0...niu-heimar-v0.13.0) (2026-11-08)


### Features

* **dashboard:** Research tab shows S2 fields, gate log linked to the timeline dots, no duplicate Claude meters; unreviewed marker only for release sessions ([#45](https://github.com/kaiserdrake/niu-heimar/issues/45)) ([47d3d94](https://github.com/kaiserdrake/niu-heimar/commit/47d3d94be2112edc65d438f361d6c2a50c383500))

## [0.12.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.11.0...niu-heimar-v0.12.0) (2026-11-08)


### Features

* **dashboard:** Inactive vs Archived topics, icon actions, agent models on the timeline ([#41](https://github.com/kaiserdrake/niu-heimar/issues/41)) ([e2ebaa1](https://github.com/kaiserdrake/niu-heimar/commit/e2ebaa186def7d5babdb4087b14a9bca93f16370))

## [0.11.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.10.0...niu-heimar-v0.11.0) (2026-11-07)


### Features

* **dashboard:** fuzzy search in the Topics panel ([#40](https://github.com/kaiserdrake/niu-heimar/issues/40)) ([282ced0](https://github.com/kaiserdrake/niu-heimar/commit/282ced052088cf3faa052e54921b0c41fe387e8b))
* **dashboard:** Topics panel to manage valkyrie-dispatch topics ([#38](https://github.com/kaiserdrake/niu-heimar/issues/38)) ([505a050](https://github.com/kaiserdrake/niu-heimar/commit/505a0500b984a2f3bcb3c1f2d8382d719e891108))

## [0.10.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.9.0...niu-heimar-v0.10.0) (2026-11-07)


### Features

* **dashboard:** mark release-please PRs and let Enter confirm a merge ([#34](https://github.com/kaiserdrake/niu-heimar/issues/34)) ([6cedf80](https://github.com/kaiserdrake/niu-heimar/commit/6cedf8005c27afe8995f2c1f669be5d76ce55bf5))
* **dashboard:** Research tab for research-dispatch, plus three dashboard fixes ([#36](https://github.com/kaiserdrake/niu-heimar/issues/36)) ([b461171](https://github.com/kaiserdrake/niu-heimar/commit/b461171a7b0bb3787421a61c2dd27063c3eb95c4))

## [0.9.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.8.1...niu-heimar-v0.9.0) (2026-11-06)


### Features

* **dashboard:** dismiss sessions the hooks never saw end ([#31](https://github.com/kaiserdrake/niu-heimar/issues/31)) ([71ae808](https://github.com/kaiserdrake/niu-heimar/commit/71ae8085a937ae1614d4592c4eab0aed0ab0a641))

## [0.8.1](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.8.0...niu-heimar-v0.8.1) (2026-11-06)


### Bug Fixes

* **dashboard:** agent graph rendered nothing after the tooltip change ([#28](https://github.com/kaiserdrake/niu-heimar/issues/28)) ([296a345](https://github.com/kaiserdrake/niu-heimar/commit/296a3456df4459b2f3068a25f2b5f32ce2cc65c9))

## [0.8.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.7.0...niu-heimar-v0.8.0) (2026-11-06)


### Features

* Claude usage API by default, themed graph tooltips, hide stale sessions ([#26](https://github.com/kaiserdrake/niu-heimar/issues/26)) ([ccbd06c](https://github.com/kaiserdrake/niu-heimar/commit/ccbd06c58490f7e3bb0f123023e0ffd240fdbeda))

## [0.7.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.6.0...niu-heimar-v0.7.0) (2026-11-06)


### Features

* Discord notifications, per-PR reviews with Sonnet agents, dashboard fixes ([#24](https://github.com/kaiserdrake/niu-heimar/issues/24)) ([2843d09](https://github.com/kaiserdrake/niu-heimar/commit/2843d09350e0d0caee34defaae44b7aa9b21aea7))

## [0.6.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.5.0...niu-heimar-v0.6.0) (2026-11-06)


### Features

* local_logs/local_ci/local_git digest tools and dashboard improvements ([#22](https://github.com/kaiserdrake/niu-heimar/issues/22)) ([3ebb53a](https://github.com/kaiserdrake/niu-heimar/commit/3ebb53a0a5ffaa6dac16f853eb596e5f60f106d1))

## [0.5.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.4.0...niu-heimar-v0.5.0) (2026-11-06)


### Features

* **guard:** live-stack guard for projects that are also the running compose stack ([#20](https://github.com/kaiserdrake/niu-heimar/issues/20)) ([81b8eb1](https://github.com/kaiserdrake/niu-heimar/commit/81b8eb198003d8c1be34d3813712de4f98a6406e))

## [0.4.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.3.0...niu-heimar-v0.4.0) (2026-11-06)


### Features

* **dashboard:** Claude 5-hour and weekly limits in an "LLMs" panel ([#18](https://github.com/kaiserdrake/niu-heimar/issues/18)) ([ec52b16](https://github.com/kaiserdrake/niu-heimar/commit/ec52b164f976dfbc7d6f42e485fa41b5662018fa))

## [0.3.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.2.0...niu-heimar-v0.3.0) (2026-11-06)


### Features

* **dashboard:** left column instead of the pull-out drawer ([#16](https://github.com/kaiserdrake/niu-heimar/issues/16)) ([ba3da86](https://github.com/kaiserdrake/niu-heimar/commit/ba3da86cb4d689d1673931ca3d49aa30dc1a5e54))

## [0.2.0](https://github.com/kaiserdrake/niu-heimar/compare/niu-heimar-v0.1.0...niu-heimar-v0.2.0) (2026-11-06)


### Features

* **dashboard:** "needs you" action badges + 4-digit merge confirmation ([#9](https://github.com/kaiserdrake/niu-heimar/issues/9)) ([04e3ed5](https://github.com/kaiserdrake/niu-heimar/commit/04e3ed5adf86de408af38339e642002597075072))
* **dashboard:** menu-based pairing, host name, animated agent graph, clearer PR blocks ([#7](https://github.com/kaiserdrake/niu-heimar/issues/7)) ([aa1c773](https://github.com/kaiserdrake/niu-heimar/commit/aa1c7738e58bb9b3a790dc5856c424c18f5f18a9))
* **dashboard:** mission control layout that fits one 1080p screen ([#13](https://github.com/kaiserdrake/niu-heimar/issues/13)) ([00a590c](https://github.com/kaiserdrake/niu-heimar/commit/00a590cfe4091fad7219d16d92bac226b18560ac))
* **dashboard:** Ollama status, real session names, agent hand-off chain ([#12](https://github.com/kaiserdrake/niu-heimar/issues/12)) ([62e59f1](https://github.com/kaiserdrake/niu-heimar/commit/62e59f14c54b9a272d50c6611ae30fd6a06d6825))
* **dashboard:** pull-out drawer, Events tab, one-screen layout down to 900x600 ([#14](https://github.com/kaiserdrake/niu-heimar/issues/14)) ([0cbab9e](https://github.com/kaiserdrake/niu-heimar/commit/0cbab9e2e7c3d64f847ecb2e2af0319335e8fb12))
* **guard:** per-project .claude/protected-paths ([#10](https://github.com/kaiserdrake/niu-heimar/issues/10)) ([0bb2010](https://github.com/kaiserdrake/niu-heimar/commit/0bb2010083d949a5176e68644b8a68934d68e392))
* initial niu-heimar Claude Code workspace setup ([75dc973](https://github.com/kaiserdrake/niu-heimar/commit/75dc97382d1183d4bc5a53fce15d55f569b32b23))
* local Ollama agents and design-first /develop workflow ([#11](https://github.com/kaiserdrake/niu-heimar/issues/11)) ([ffc5f97](https://github.com/kaiserdrake/niu-heimar/commit/ffc5f97c4a6cde4df2b5a7767ddc10f3cfc7102a))
* web dashboard (niu-dash) with interaction graph, timeline and PR merge ([#6](https://github.com/kaiserdrake/niu-heimar/issues/6)) ([44b3dcd](https://github.com/kaiserdrake/niu-heimar/commit/44b3dcdf78a8d36cb3c5aff7fefcaa231020eeb6))


### Performance Improvements

* **dashboard:** shared incremental state cache with adaptive throttle ([#8](https://github.com/kaiserdrake/niu-heimar/issues/8)) ([f51ddf3](https://github.com/kaiserdrake/niu-heimar/commit/f51ddf3a24985258a0ff96bf29fd89e7581a9a0e))
