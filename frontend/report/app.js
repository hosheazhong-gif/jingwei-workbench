(function () {
  const params = new URLSearchParams(window.location.search);
  let projectId = params.get("project");
  let bench = null;
  let selectedQuestionId = null;
  let selectedBlockId = null;
  let highlightSourceId = null;
  let selectedMaterialId = null;
  // 勾上的未标材料：id -> "source" / "candidate"。只记人自己勾的，不替人猜。
  let bulkPicks = {};
  // 正在对上一版的那一节。候选默认就显示在稿上，人要能看见换掉了什么。
  let comparingBlockId = null;
  // 展开看全文的长原话。存的是显示状态，不改库里的字。
  let expandedExcerpts = {};
  const EXCERPT_PREVIEW_CHARS = 140;
  const GIST_CHARS = 56;
  // 稿默认只铺开选中的那一节；要通读整篇再点「全部展开」。
  let expandAllBlocks = false;
  let feedbackOpen = false;
  // 正在回看的上一轮。只读，不能改；改要回到当前轮。
  let lookbackRound = null;
  let pendingDecision = null;
  let draftingDecision = false;
  // 收起来的分组。只是显示状态，存在本机，不进账本。
  let collapsedGroups = readCollapsed();
  let unsourcedNote = null;
  let dismissedPreview = {};
  let addFileOpen = false;
  let addLinkOpen = false;
  let addingQuestion = false;
  let createOpen = false;
  let writingBlockId = null;
  let editingBlockId = null;
  let editingMandate = false;
  let editingTitleBlockId = null;
  let supersedeSourceId = null;
  let excerptingSourceId = null;
  let excerptClientProvided = false;
  let hangingExcerpt = false;
  let pendingDeleteId = null;
  let homeTidying = false;
  let homeListing = null;
  let addingSection = false;
  let sectionMoreOpen = false;
  let selectedExcerptClaimId = null;
  // 哪一节的原话是摊开的。原话默认收起，它是佐证不是正文。
  let excerptsOpenBlockId = null;
  // 哪一节的数字清单是摊开的。默认收起。
  let numbersOpenBlockId = null;
  let impactOpenBlockId = null;
  let searchingMaterials = false;
  let splittingQuestions = false;
  let closingRound = false;
  let pendingRoundQuestions = null;
  let pendingSnapshotExcerpts = null;
  let scrapingSourceId = null;
  let editingQuestionId = null;

  const PLACEHOLDER_TEXT = "这一节还没写。";
  const home = document.getElementById("home");
  const homeBoard = document.getElementById("home-board");
  const homeCreate = document.getElementById("home-create");
  // 模板清单只读一次；选定后不再更换（模板影响拆问题时的提示，
  // 中途换模板会让已收下的问题和新提示对不上）。
  let templateChoices = null;
  let templateDefaultKey = null;
  let templateNote = "";
  let templateOutOfScope = [];
  let templateSourceTraps = [];
  // 去掉材料要先点一次再确认一次，跟去掉题目同一个写法。
  let pendingSourceRemovalId = null;
  const homeNew = document.getElementById("home-new");
  const homeGuide = document.getElementById("home-guide");
  const guideRoot = document.getElementById("guide");
  // 模板介绍页只描述已经安装的模板，不是外部知识库。
  let guideOpen = false;
  let guideKey = null;
  const homeTidy = document.getElementById("home-tidy");
  const benchRoot = document.getElementById("bench");
  const projectName = document.getElementById("project-name");
  const projectTemplate = document.getElementById("project-template");
  const mandateRoot = document.getElementById("mandate");
  const flash = document.getElementById("flash");
  const questionsRoot = document.getElementById("questions");
  const draftRoot = document.getElementById("draft");
  const materialsRoot = document.getElementById("materials");
  const exportWord = document.getElementById("export-word");
  const exportDetailed = document.getElementById("export-detailed");
  const goHome = document.getElementById("go-home");

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      const value = attrs[key];
      if (key === "className") node.className = value;
      else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (value === true) node.setAttribute(key, "");
      else if (value !== false && value != null) node.setAttribute(key, String(value));
    });
    (children || []).forEach(function (child) {
      if (child == null || child === false) return;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }

  function text(value) {
    return value == null ? "" : String(value);
  }

  function showFlash(message, isError) {
    if (!message) {
      flash.hidden = true;
      flash.textContent = "";
      return;
    }
    flash.hidden = false;
    flash.className = "flash" + (isError ? " error" : "");
    flash.textContent = message;
  }

  function explainHttpError(error, fallback) {
    const raw = text(error && error.message ? error.message : error);
    if (raw.indexOf("未找到该接口") >= 0) {
      return "看稿进程还是旧的。再运行一次 serve，新进程会顶掉旧的。";
    }
    return raw || fallback || "没有完成。";
  }

  function readBody(response) {
    return response.text().then(function (raw) {
      var payload;
      try {
        payload = JSON.parse(raw);
      } catch (err) {
        throw new Error("看稿进程还是旧的。再运行一次 serve，新进程会顶掉旧的。");
      }
      if (!response.ok) throw new Error(payload.error || "没有完成。");
      return payload;
    });
  }

  function readJson(path) {
    return fetch(path).then(readBody);
  }

  function writeJson(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(readBody);
  }

  function writeDelete(path) {
    return fetch(path, { method: "DELETE" }).then(readBody);
  }

  function setProject(id) {
    projectId = id;
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("project", id);
    else url.searchParams.delete("project");
    window.history.replaceState({}, "", url);
  }

  function showHome() {
    setProject(null);
    pendingDeleteId = null;
    homeTidying = false;
    home.hidden = false;
    benchRoot.hidden = true;
    document.body.classList.remove("mode-bench");
    createOpen = false;
    renderCreateForm();
    showFlash("");
    loadTemplateChoices(function () {
      if (createOpen) renderCreateForm();
    });
    readJson("/projects")
      .then(renderHome)
      .catch(function (error) {
        homeBoard.replaceChildren(el("p", { className: "empty" }, [explainHttpError(error)]));
      });
  }

  function templatePicker() {
    // 只有一个模板时不摆选择器：选不选都一样，多一栏只是噪音。
    if (!templateChoices || templateChoices.length < 2) return null;
    // 显式选中默认那一项。只靠「默认模板排第一」不够稳：排序一改，
    // 人不动下拉框就会静默拿到另一套问法（2026-08-23 走查踩到过）。
    const options = templateChoices.map(function (item) {
      const chosen = templateDefaultKey ? item.key === templateDefaultKey : !!item.is_default;
      // 没走查过的在选的时候就要看得出来，不能等人建完题目才发现。
      return el("option", {
        value: item.key,
        title: text(item.brief_prompt),
        selected: chosen,
      }, [text(item.name) + (item.loop_walked ? "" : "（" + text(item.status_label) + "）")]);
    });
    // 提示条就地改文字，不重画整个表单——重画会把人已经打好的题目和
    // 那句话清空。
    const warning = el("p", { className: "note untried-note" }, [""]);
    function showWarning(key) {
      const current = templateChoices.filter(function (item) {
        return item.key === key;
      })[0];
      const untried = current && !current.loop_walked;
      warning.textContent = untried ? text(current.status_note) : "";
      warning.hidden = !untried;
    }
    const picker = el("select", {
      name: "template_key",
      id: "template-key",
      onChange: function (event) { showWarning(event.target.value); },
    }, options);
    if (templateDefaultKey) picker.value = templateDefaultKey;
    showWarning(picker.value);
    return el("div", { className: "template-pick" }, [
      el("label", { className: "template-pick-label", for: "template-key" }, [
        "按哪类活来拆问题",
      ]),
      picker,
      warning,
      el("p", { className: "note" }, [
        "选定后这道题不再换：模板只影响拆问题时给模型的参考提示，换了会跟已经收下的问题对不上。",
      ]),
    ]);
  }

  function loadTemplateChoices(done) {
    if (templateChoices !== null) {
      if (done) done();
      return;
    }
    readJson("/templates")
      .then(function (payload) {
        templateChoices = (payload && payload.templates) || [];
        templateDefaultKey = (payload && payload.default_key) || null;
        templateNote = (payload && payload.limitation) || "";
        templateOutOfScope = (payload && payload.out_of_scope) || [];
        templateSourceTraps = (payload && payload.source_traps) || [];
        if (done) done();
      })
      .catch(function () {
        // 取不到就退回只用默认模板，不挡住新建题目这条路。
        templateChoices = [];
        if (done) done();
      });
  }

  function renderGuide() {
    if (!guideRoot) return;
    document.body.classList.toggle("mode-guide", guideOpen);
    if (!guideOpen) {
      guideRoot.hidden = true;
      guideRoot.replaceChildren();
      return;
    }
    guideRoot.hidden = false;
    if (templateChoices === null) {
      guideRoot.replaceChildren(el("p", { className: "empty" }, ["正在读取模板…"]));
      return;
    }
    if (!templateChoices.length) {
      guideRoot.replaceChildren(el("p", { className: "empty" }, ["没有读到模板。"]));
      return;
    }
    const picked =
      templateChoices.filter(function (x) { return x.key === guideKey; })[0]
      || templateChoices[0];
    guideKey = picked.key;
    const rows = [];
    // 模板多于一个才摆切换条，只有一个时它是噪音。
    if (templateChoices.length > 1) {
      rows.push(
        el("div", { className: "guide-tabs" }, templateChoices.map(function (item) {
          return el("button", {
            className: "guide-tab" + (item.key === guideKey ? " current" : ""),
            type: "button",
            onClick: function () {
              guideKey = item.key;
              renderGuide();
            },
          }, [text(item.name)]);
        }))
      );
    }
    rows.push(el("h2", { className: "guide-name" }, [
      text(picked.name),
      picked.is_default ? el("span", { className: "guide-tag" }, ["默认"]) : null,
      // 走查状态必须挨着名字。没走查过的模板在页面上跟走查过的长得一样，
      // 使用者分不出哪条问法有来处、哪条是拟的。
      el("span", {
        className: "guide-status " + text(picked.verification || "skeleton"),
      }, [text(picked.status_label || "")]),
    ]));
    if (picked.status_note) {
      rows.push(el("p", {
        className: "guide-status-note" + (picked.loop_walked ? "" : " untried"),
      }, emphasized(text(picked.status_note))));
    }
    if (picked.intro) {
      rows.push(el("div", { className: "guide-intro" }, paragraphs(picked.intro)));
    }
    const use = picked.when_to_use || [];
    const avoid = picked.when_not_to_use || [];
    if (use.length || avoid.length) {
      rows.push(el("div", { className: "guide-fit" }, [
        use.length ? guideList("什么时候用它", use, "fit-use") : null,
        avoid.length ? guideList("什么时候别用它", avoid, "fit-avoid") : null,
      ]));
    }
    if ((picked.flow || []).length) {
      rows.push(el("p", { className: "guide-label" }, ["整条循环长什么样"]));
      rows.push(guideFlow(picked.flow));
    }
    if ((picked.steps || []).length) {
      rows.push(el("p", { className: "guide-label" }, ["一步一步怎么做"]));
      rows.push(el("ol", { className: "guide-steps" }, picked.steps.map(function (s) {
        return el("li", {}, [
          el("strong", {}, [text(s.title)]),
          s.detail ? el("p", { className: "guide-step-detail" }, emphasized(text(s.detail))) : null,
          s.done_when
            ? el("p", { className: "guide-done" }, ["做完的标志：" + text(s.done_when)])
            : null,
        ]);
      })));
    }
    // 七条问法逐条挂来处。这个工具要求主张挂来源，模板自己的问法没道理例外。
    const questions = picked.questions || [];
    if (questions.length) {
      rows.push(el("p", { className: "guide-label" }, ["拆问题时会参考的七条，各自从哪来"]));
      rows.push(el("ol", { className: "guide-questions" }, questions.map(function (q) {
        return el("li", {}, [
          el("p", { className: "guide-question" }, [text(q.label)]),
          el("p", { className: "guide-question-source" }, ["来处："].concat(emphasized(text(q.source)))),
        ]);
      })));
    }
    const ex = picked.example || {};
    if (ex.brief) {
      rows.push(el("p", { className: "guide-label" }, ["一个完整的例子"]));
      rows.push(guideExample(ex));
    }
    if ((picked.pitfalls || []).length) {
      rows.push(el("p", { className: "guide-label" }, ["常见的坑"]));
      rows.push(el("ul", { className: "guide-pitfalls" }, picked.pitfalls.map(function (x) {
        return el("li", {}, emphasized(text(x)));
      })));
    }
    // 试问法时撞出来的来源陷阱。跟具体模板无关，是这条循环本身的盲点：
    // 硬门槛只管「原话逐字来自快照」，管不住这个来源本身算不算数。
    if ((templateSourceTraps || []).length) {
      rows.push(el("p", { className: "guide-label" }, ["挑材料时最容易踩的三样"]));
      rows.push(el("ul", { className: "guide-out" }, templateSourceTraps.map(function (item) {
        return el("li", {}, [
          el("strong", {}, [text(item.trap)]),
          el("p", { className: "guide-out-why" }, emphasized(text(item.why))),
        ]);
      })));
    }
    // 说清做不了什么，比让人自己撞上去省事。这一段跟具体模板无关，摆在最后。
    if ((templateOutOfScope || []).length) {
      rows.push(el("p", { className: "guide-label" }, ["这些活现在装不进来"]));
      rows.push(el("ul", { className: "guide-out" }, templateOutOfScope.map(function (item) {
        return el("li", {}, [
          el("strong", {}, [text(item.work)]),
          el("p", { className: "guide-out-why" }, emphasized(text(item.why))),
        ]);
      })));
    }
    rows.push(el("p", { className: "guide-note" }, [
      text(templateNote || "模板只影响拆本轮问题时给模型的参考提示。"),
    ]));
    guideRoot.replaceChildren.apply(guideRoot, rows.filter(Boolean));
  }

  function guideList(title, items, className) {
    return el("div", { className: "guide-fit-col " + className }, [
      el("p", { className: "guide-fit-title" }, [title]),
      el("ul", {}, items.map(function (x) { return el("li", {}, emphasized(text(x))); })),
    ]);
  }

  // 竖向流程图。不引任何库：方块 + 箭头，人工关卡单独标出来，
  // 因为「人点收下才算数」是这个产品最要紧的一条，不能画得跟别的步骤一样。
  function guideFlow(flow) {
    const nodes = [];
    flow.forEach(function (item, index) {
      if (index) nodes.push(el("div", { className: "flow-arrow" }, ["↓"]));
      nodes.push(
        el("div", { className: "flow-node" + (item.gate ? " gate" : "") }, [
          el("p", { className: "flow-stage" }, [
            item.gate ? el("span", { className: "flow-gate-mark" }, ["人工"]) : null,
            text(item.stage),
          ]),
          item.detail ? el("p", { className: "flow-detail" }, [text(item.detail)]) : null,
        ])
      );
    });
    nodes.push(el("p", { className: "flow-loop" }, ["↻ 收口这一轮，带着经理反馈回到「拆本轮问题」，开下一轮"]));
    return el("div", { className: "guide-flow" }, nodes);
  }

  function guideExample(ex) {
    const rows = [
      el("p", { className: "guide-ex-label" }, ["经理原话"]),
      el("p", { className: "guide-ex-brief" }, [text(ex.brief)]),
    ];
    if ((ex.questions || []).length) {
      rows.push(el("p", { className: "guide-ex-label" }, ["拆出来大概是这几条"]));
      rows.push(el("ol", {}, ex.questions.map(function (q) {
        return el("li", {}, [text(q)]);
      })));
    }
    if (ex.material) {
      rows.push(el("p", { className: "guide-ex-label" }, ["材料从哪来"]));
      rows.push(el("p", {}, [text(ex.material)]));
    }
    if (ex.draft) {
      rows.push(el("p", { className: "guide-ex-label" }, ["写出来的稿长这样"]));
      rows.push(el("pre", { className: "guide-ex-draft" }, [text(ex.draft)]));
    }
    if (ex.note) rows.push(el("p", { className: "guide-ex-note" }, [text(ex.note)]));
    return el("div", { className: "guide-example" }, rows);
  }

  function paragraphs(value) {
    return text(value).split("\n").filter(function (line) {
      return line.trim();
    }).map(function (line) {
      return el("p", {}, emphasized(line));
    });
  }

  // 模板介绍里用 **两个星号** 标边界句（「这一段不在这个模板里」）。
  // 原来直接当纯文本渲染，星号露在页面上，边界句反而比正文更难看清。
  // 只认这一种记号，不引 Markdown：这里要的是一处强调，不是一套语法。
  function emphasized(line) {
    const parts = String(line).split("**");
    if (parts.length < 3) return [line];
    return parts.map(function (part, index) {
      if (!part) return null;
      return index % 2 ? el("strong", {}, [part]) : part;
    }).filter(function (node) { return node !== null; });
  }

  function renderCreateForm() {
    if (!createOpen) {
      homeCreate.hidden = true;
      homeCreate.replaceChildren();
      return;
    }
    homeCreate.hidden = false;
    homeCreate.replaceChildren(
      el("form", {
        onSubmit: function (event) {
          event.preventDefault();
          const name = event.target.querySelector("[name=name]").value.trim();
          const context = event.target.querySelector("[name=context]").value.trim();
          const picker = event.target.querySelector("[name=template_key]");
          const body = { name: name, original_context: context };
          if (picker && picker.value) body.template_key = picker.value;
          writeJson("/projects", body)
            .then(function (payload) {
              createOpen = false;
              openProject(payload.project_id, name);
            })
            .catch(function (error) {
              homeCreate.appendChild(
                el("p", { className: "note" }, [explainHttpError(error)])
              );
            });
        },
      }, [
        el("input", { name: "name", type: "text", placeholder: "题目名称", required: true }),
        el("textarea", { name: "context", rows: "3", placeholder: "经理这轮要判断什么", required: true }),
        templatePicker(),
        el("div", { className: "action-row" }, [
          el("button", { className: "primary", type: "submit" }, ["开始"]),
          el("button", {
            className: "quiet",
            type: "button",
            onClick: function () {
              createOpen = false;
              renderCreateForm();
            },
          }, ["取消"]),
        ]),
      ])
    );
  }

  function renderHome(listing) {
    homeListing = listing;
    const items = (listing && listing.projects) || [];
    if (homeTidy) {
      homeTidy.hidden = !items.length;
      homeTidy.textContent = homeTidying ? "收起" : "整理题目";
    }
    if (!items.length) {
      homeTidying = false;
      homeBoard.replaceChildren(el("p", { className: "empty" }, ["还没有题目。"]));
      return;
    }
    homeBoard.replaceChildren.apply(
      homeBoard,
      items.map(function (item) {
        const decision = text(item.decision || item.original_context);
        const pending = homeTidying && pendingDeleteId === item.id;
        const row = [
          el("button", {
            className: "home-open",
            type: "button",
            onClick: function () {
              pendingDeleteId = null;
              homeTidying = false;
              openProject(item.id, item.name);
            },
          }, [
            el("strong", {}, [text(item.name)]),
            decision ? el("span", { className: "home-decision" }, [decision]) : null,
            // 建完题目之后模板就不再露面，过两周回来只能靠题目名猜用的是哪套
            // 问法（2026-08-23 流水账）。列表每行都写出来。
            item.template_name
              ? el("span", { className: "home-template" }, [text(item.template_name)])
              : null,
          ]),
        ];
        if (homeTidying) {
          row.push(
            pending
              ? el("div", { className: "action-row" }, [
                  el("button", {
                    className: "ghost",
                    type: "button",
                    onClick: function (event) {
                      event.stopPropagation();
                      removeProject(item.id);
                    },
                  }, ["确认去掉"]),
                  el("button", {
                    className: "quiet",
                    type: "button",
                    onClick: function (event) {
                      event.stopPropagation();
                      pendingDeleteId = null;
                      renderHome(listing);
                    },
                  }, ["取消"]),
                ])
              : el("button", {
                  className: "quiet",
                  type: "button",
                  onClick: function (event) {
                    event.stopPropagation();
                    pendingDeleteId = item.id;
                    renderHome(listing);
                  },
                }, ["去掉"])
          );
        }
        return el("div", { className: "home-row" }, row);
      })
    );
  }

  function removeProject(id) {
    writeDelete("/projects/" + encodeURIComponent(id))
      .then(function () {
        pendingDeleteId = null;
        return readJson("/projects");
      })
      .then(function (listing) {
        setProject(null);
        home.hidden = false;
        benchRoot.hidden = true;
        renderHome(listing);
        showFlash("已去掉这道题目。");
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function openProject(id, name) {
    setProject(id);
    selectedQuestionId = null;
    selectedBlockId = null;
    highlightSourceId = null;
    selectedMaterialId = null;
    unsourcedNote = null;
    dismissedPreview = {};
    addingQuestion = false;
    writingBlockId = null;
    editingBlockId = null;
    editingMandate = false;
    editingTitleBlockId = null;
    addingSection = false;
    sectionMoreOpen = false;
    selectedExcerptClaimId = null;
    supersedeSourceId = null;
    excerptingSourceId = null;
    excerptClientProvided = false;
    hangingExcerpt = false;
    searchingMaterials = false;
    closingRound = false;
    home.hidden = true;
    benchRoot.hidden = false;
    document.body.classList.add("mode-bench");
    if (name) projectName.textContent = text(name);
    loadBench();
  }

  function retryButton() {
    return el("button", {
      className: "primary",
      type: "button",
      onClick: loadBench,
    }, ["再试一次"]);
  }

  function loadBench() {
    return readJson("/projects/" + encodeURIComponent(projectId) + "/workbench")
      .then(function (payload) {
        bench = payload;
        if (!selectedBlockId && payload.blocks.length) {
          selectedBlockId = payload.blocks[0].id;
        }
        showFlash("");
        renderBench();
      })
      .catch(function (error) {
        bench = null;
        showFlash(explainHttpError(error), true);
        renderFailedBench();
      });
  }

  function renderFailedBench() {
    questionsRoot.replaceChildren(
      el("p", { className: "empty" }, ["本轮问题还没进来。"]),
      retryButton()
    );
    draftRoot.replaceChildren(
      el("p", { className: "empty" }, ["给经理的稿还没进来。"])
    );
    materialsRoot.replaceChildren(
      el("p", { className: "empty" }, ["材料匣还没进来。"])
    );
  }

  function renderBench() {
    projectName.textContent = text(bench.project.name);
    // 这道题按哪套问法在拆，顶栏要一直看得见。
    const templateName = text(bench.project.template_name);
    projectTemplate.textContent = templateName ? "按「" + templateName + "」拆问题" : "";
    projectTemplate.hidden = !templateName;
    renderMandate();
    renderQuestions();
    renderDraft();
    renderMaterials();
  }

  // 每一轮是一块自己的台面：点标签整块换过去，上一轮只读。
  // 第二轮起：读上一轮的稿、上一轮问题和经理反馈，先拟这一轮要决定什么。
  function mandateDraftButton() {
    if (((bench && bench.current_round) || 1) < 2) return null;
    return el("button", {
      className: "ghost",
      type: "button",
      disabled: draftingDecision,
      onClick: function () {
        if (draftingDecision) return;
        draftingDecision = true;
        showFlash("将发送：上一轮的稿、上一轮问题、经理反馈。正在拟…");
        renderMandate();
        writeJson(
          "/projects/" + encodeURIComponent(projectId) + "/round-decision/draft",
          {}
        )
          .then(function (payload) {
            pendingDecision = payload.decision || "";
            showFlash(payload.confirmation && payload.confirmation.message);
          })
          .catch(function (error) {
            showFlash(explainHttpError(error), true);
          })
          .then(function () {
            draftingDecision = false;
            renderMandate();
          });
      },
    }, [draftingDecision ? "正在拟…" : "按上一轮和反馈拟这一轮"]);
  }

  function roundTabs() {
    const rounds = ((bench && bench.archived_rounds) || []).map(function (item) {
      return { index: item.round_index, label: item.round_label, archived: true };
    });
    const current = (bench && bench.current_round) || 1;
    rounds.push({
      index: current,
      label: (bench && bench.round_label) || "第 " + current + " 轮",
      archived: false,
    });
    if (rounds.length < 2) return null;
    const active = lookbackRound || current;
    return el("div", { className: "round-tabs" }, rounds.map(function (item) {
      return el("button", {
        className: "round-tab" + (item.index === active ? " current" : ""),
        type: "button",
        title: item.archived ? "看这一轮当时的问题、稿和材料（只读）" : "回到在写的这一轮",
        onClick: function () {
          lookbackRound = item.archived ? item.index : null;
          selectedQuestionId = null;
          selectedBlockId = null;
          renderBench();
        },
      }, [item.label + (item.archived ? "" : "・在写")]);
    }));
  }

  function setChildren(root) {
    root.replaceChildren.apply(
      root,
      keepNodes(Array.prototype.slice.call(arguments, 1))
    );
  }

  function renderMandate() {
    const label = el("span", { className: "mandate-label" }, [
      text((bench && bench.round_label) || "这轮") + "要决定",
    ]);
    if (lookbackRound) {
      const round = lookbackData();
      setChildren(
        mandateRoot,
        roundTabs(),
        el("span", { className: "mandate-label" }, [
          text((round ? round.round_label : "上一轮") + "已收口，只读",
        )]),
        el("span", { className: "mandate-text-plain" }, [
          text(bench && bench.decision) || "",
        ])
      );
      return;
    }
    const current = text(bench && bench.decision) || "这轮还没写下要决定什么。";
    if (editingMandate) {
      const field = el("input", { type: "text", value: text(bench && bench.decision) });
      setChildren(
        mandateRoot,
        roundTabs(),
        label,
        field,
        el("div", { className: "action-row" }, [
          el("button", {
            className: "primary",
            type: "button",
            onClick: function () {
              saveMandate(field.value);
            },
          }, ["记下"]),
          el("button", {
            className: "quiet",
            type: "button",
            onClick: function () {
              editingMandate = false;
              renderMandate();
            },
          }, ["取消"]),
        ])
      );
      field.focus();
      return;
    }
    if (pendingDecision) {
      const field = el("input", { type: "text", value: text(pendingDecision) });
      setChildren(
        mandateRoot,
        roundTabs(),
        label,
        el("span", { className: "hint" }, ["模型拟的这一轮要决定什么。收下才算数。"]),
        field,
        el("div", { className: "action-row" }, [
          el("button", {
            className: "primary",
            type: "button",
            onClick: function () {
              pendingDecision = null;
              saveMandate(field.value);
            },
          }, ["收下"]),
          el("button", {
            className: "quiet",
            type: "button",
            onClick: function () {
              pendingDecision = null;
              renderMandate();
            },
          }, ["丢掉"]),
        ])
      );
      return;
    }
    setChildren(
      mandateRoot,
      roundTabs(),
      label,
      mandateDraftButton(),
      el("button", {
        className: "mandate-text",
        id: "mandate-text",
        type: "button",
        onClick: function () {
          editingMandate = true;
          renderMandate();
        },
      }, [current])
    );
  }

  function saveMandate(value) {
    const next = text(value).trim();
    if (!next) {
      showFlash("这轮要决定什么不能空着。", true);
      return;
    }
    writeJson("/projects/" + encodeURIComponent(projectId) + "/brief", {
      decision_question: next,
    })
      .then(function (payload) {
        editingMandate = false;
        showFlash(payload.confirmation && payload.confirmation.message);
        return loadBench();
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function lookbackQuestionCard(item) {
    const open = selectedQuestionId === item.id;
    return el("div", { className: "question archived" + (open ? " current" : "") }, [
      el("button", {
        className: "question-title",
        type: "button",
        onClick: function () {
          selectedQuestionId = open ? null : item.id;
          renderBench();
        },
        title: text(item.question),
      }, [questionTitle(item)]),
      el("p", { className: "why" }, [text(item.progress_label)]),
      questionTargetRow(item, false),
      open ? el("p", { className: "why full-question" }, [text(item.question)]) : null,
      open && item.why_it_matters
        ? el("p", { className: "why" }, [text(item.why_it_matters)])
        : null,
    ]);
  }

  function renderLookbackQuestions() {
    const round = lookbackData();
    const all = (round && round.questions) || [];
    // 这一轮真正在用的，和当时点掉／被新候选换掉的，分开列。混在一起时那些
    // 旧稿看着就像别的轮次的问题串了进来（第 1 轮 5 条在用、8 条没用过）。
    const rows = all.filter(function (item) { return !item.deferred; });
    const aside = all.filter(function (item) { return item.deferred; });
    const nodes = [];
    if (!rows.length) {
      nodes.push(el("p", { className: "empty" }, ["这一轮没有在用的问题。"]));
    }
    rows.forEach(function (item) {
      nodes.push(lookbackQuestionCard(item));
    });
    if (aside.length) {
      const key = "q:aside:" + (round ? round.round_index : 0);
      nodes.push(groupHeading(key, "这一轮没用上的（点掉或被换掉）", aside.length));
      if (!groupFolded(key)) {
        aside.forEach(function (item) {
          nodes.push(lookbackQuestionCard(item));
        });
      }
    }
    questionsRoot.replaceChildren.apply(questionsRoot, keepNodes(nodes));
  }

  // 「这条问题落在稿的哪一节」：先立骨架再找料。定不了就留空，
  // 页面上写「还没定」——不替人猜，跟材料归属那条规矩一致。
  function questionTargetRow(item, open) {
    const blocks = (bench && bench.blocks) || [];
    const label = item.target_section
      ? "写进：" + item.target_section
      : "还没定落在哪一节";
    if (!open || !blocks.length) {
      return el(
        "p",
        { className: "why question-target" + (item.target_section ? "" : " undecided") },
        [label]
      );
    }
    const options = [
      el("option", { value: "" }, ["还没定落在哪一节"]),
    ];
    blocks.forEach(function (block) {
      const option = el("option", { value: block.id }, [text(block.title)]);
      if (block.id === item.target_block_id) option.selected = true;
      options.push(option);
    });
    return el("div", { className: "question-target-edit" }, [
      el("span", { className: "meta" }, ["写进哪一节"]),
      el(
        "select",
        {
          value: item.target_block_id || "",
          onChange: function (event) {
            setQuestionTarget(item.id, event.target.value);
          },
        },
        options
      ),
    ]);
  }

  function setQuestionTarget(questionId, blockId) {
    writeJson(
      "/research-questions/" + encodeURIComponent(questionId) + "/target-block",
      { target_block_id: blockId || null }
    )
      .then(function (payload) {
        showFlash(payload.confirmation && payload.confirmation.message);
        return loadBench();
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function renderQuestions() {
    if (lookbackRound) {
      renderLookbackQuestions();
      return;
    }
    const items = (bench && bench.questions) || [];
    let deferred = (bench && bench.deferred_questions) || [];
    const archived = (bench && bench.archived_rounds) || [];
    const nodes = [splitQuestionControls()];
    if (pendingRoundQuestions && pendingRoundQuestions.length) {
      nodes.push(pendingQuestionPreview());
    }
    if (!items.length && !deferred.length && !addingQuestion && !pendingRoundQuestions) {
      nodes.push(el("p", { className: "empty" }, [
        archived.length
          ? "上一轮已收口。拆出下一轮要回答的问题。"
          : "这轮还没写下要回答的问题。",
      ]));
      nodes.push(addQuestionControls());
      nodes.push(roundCloseControls(archived));
    nodes.push(roundReopenControls());
      questionsRoot.replaceChildren.apply(questionsRoot, keepNodes(nodes));
      return;
    }
    items.forEach(function (item) {
      const open = selectedQuestionId === item.id;
      const editing = editingQuestionId === item.id;
      // 第一层只留题目和进度；「这轮先不用 / 改这条」点开后才显示。
      const actions = [];
      if (open && item.can_defer) {
        actions.push(
          el("button", {
            className: "ghost",
            type: "button",
            onClick: function () {
              deferQuestion(item.id);
            },
          }, ["这轮先不用"])
        );
      }
      if (open && !editing) {
        actions.push(
          el("button", {
            className: "quiet",
            type: "button",
            onClick: function () {
              editingQuestionId = item.id;
              selectedQuestionId = item.id;
              renderQuestions();
            },
          }, ["改这条"])
        );
      }
      nodes.push(
        el("div", { className: "question" + (open ? " current" : "") + (item.progress === "enough" ? " enough" : "") }, [
          editing
            ? questionRenameForm(item)
            : el("button", {
                className: "question-title",
                type: "button",
                onClick: function () {
                  selectedQuestionId = open ? null : item.id;
                  bulkPicks = {};
                  editingQuestionId = null;
                  renderQuestions();
                  renderMaterials();
                  renderDraft();
                },
                title: text(item.question),
              }, [questionTitle(item)]),
          el("div", { className: "progress-row" }, [progressControl(item)]),
          questionTargetRow(item, open),
          open && questionTitle(item) !== text(item.question)
            ? el("p", { className: "why full-question" }, [text(item.question)])
            : null,
          open && item.why_it_matters
            ? el("p", { className: "why" }, [text(item.why_it_matters)])
            : null,
          actions.length ? el("div", { className: "action-row" }, actions) : null,
        ])
      );
    });
    if (deferred.length) {
      nodes.push(groupHeading("q:deferred", "这轮先不用的", deferred.length));
      if (groupFolded("q:deferred")) deferred = [];
      deferred.forEach(function (item) {
        nodes.push(
          el("div", { className: "question deferred" }, [
            el("span", { className: "question-title", title: text(item.question) }, [questionTitle(item)]),
            el("div", { className: "action-row" }, [
              el("button", {
                className: "ghost",
                type: "button",
                onClick: function () {
                  restoreQuestion(item.id);
                },
              }, ["这轮再用"]),
            ]),
          ])
        );
      });
    }
    nodes.push(addQuestionControls());
    nodes.push(roundCloseControls(archived));
    // 上一轮不再堆在这一轮下面：顶上的轮次标签一点就整块换过去。
    questionsRoot.replaceChildren.apply(questionsRoot, keepNodes(nodes));
  }

  // 收口不是单向门：只要这一轮还什么都没干，就能退回上一轮继续。
  function roundReopenControls() {
    const current = (bench && bench.current_round) || 1;
    if (current < 2) return null;
    if (((bench && bench.questions) || []).length) return null;
    // 「这轮还没开始」是退回上一轮，不是这一栏里的普通动作，得看得出来
    // （流水账第 3 条：它跟本轮问题的其他框颜色字体一样，分不出来）。
    return el("div", { className: "round-reopen" }, [
      el("p", { className: "hint" }, [
        "这一轮还没有问题。可以先拟一轮，也可以退回上一轮继续。",
      ]),
      el("button", {
        className: "round-reopen-button",
        type: "button",
        onClick: function () {
          writeJson(
            "/projects/" + encodeURIComponent(projectId) + "/rounds/reopen",
            {}
          )
            .then(function (payload) {
              showFlash(payload.confirmation && payload.confirmation.message);
              lookbackRound = null;
              selectedQuestionId = null;
              return loadBench();
            })
            .catch(function (error) {
              showFlash(explainHttpError(error), true);
            });
        },
      }, ["退回第 " + (current - 1) + " 轮继续"]),
    ]);
  }

  // 收口是一次性的换轮动作，跟「再补一条问题」不是一类事。
  // 放进单独的收尾区，跟上面的按钮隔开，也写清楚按下去会发生什么。
  function roundCloseControls(archived) {
    if (!(bench && bench.can_close_round)) return null;
    const current = (bench && bench.current_round) || 1;
    return el("div", { className: "round-close" }, [
      el("p", { className: "hint" }, [
        text(
          "收口之后，第 " + current + " 轮的问题、材料和稿会收进「第 "
            + current + " 轮」文件夹，只能回看，不能再改。"
        ),
      ]),
      el("button", {
        className: "round-close-button",
        type: "button",
        disabled: closingRound,
        onClick: closeRound,
      }, [
        closingRound
          ? "正在收口…"
          : "收口第 " + current + " 轮，开第 " + (current + 1) + " 轮",
      ]),
    ]);
  }

  function closeRound() {
    if (closingRound) return;
    closingRound = true;
    renderQuestions();
    writeJson("/projects/" + encodeURIComponent(projectId) + "/rounds/close", {})
      .then(function (payload) {
        bench = payload.workbench;
        closingRound = false;
        pendingRoundQuestions = null;
        selectedQuestionId = null;
        showFlash(payload.confirmation && payload.confirmation.message);
        renderBench();
      })
      .catch(function (error) {
        closingRound = false;
        showFlash(explainHttpError(error), true);
        renderQuestions();
      });
  }

  function splitQuestionControls() {
    return el("div", { className: "add-line" }, [
      el("button", {
        className: "ghost",
        type: "button",
        disabled: splittingQuestions,
        onClick: splitRoundQuestions,
      }, [splittingQuestions ? "正在拆…" : "按这句话拆问题"]),
    ]);
  }

  function pendingQuestionPreview() {
    const rows = pendingRoundQuestions.map(function (item) {
      return el("li", {}, [
        text(item.question),
        item.enough_for_now
          ? el("p", { className: "why" }, [text(item.enough_for_now)])
          : null,
        // 模型顺手给的落点。对不上现有小节的会在收下时留空，不猜。
        el("p", { className: "why question-target" + (item.section ? "" : " undecided") }, [
          item.section ? "写进：" + text(item.section) : "没说落在哪一节",
        ]),
      ]);
    });
    return el("div", { className: "question-preview" }, [
      el("p", { className: "hint" }, ["模型先拟，收下才进左栏。"]),
      el("ul", { className: "question-preview-list" }, rows),
      el("div", { className: "action-row" }, [
        el("button", {
          className: "primary",
          type: "button",
          onClick: adoptRoundQuestions,
        }, ["收下"]),
        el("button", {
          className: "quiet",
          type: "button",
          onClick: function () {
            pendingRoundQuestions = null;
            renderQuestions();
          },
        }, ["丢掉"]),
      ]),
    ]);
  }

  function questionRenameForm(item) {
    const labelField = el("input", {
      type: "text",
      name: "label",
      value: text(item.label || ""),
      placeholder: "短名（左栏只显示这个）",
    });
    const field = el("input", {
      type: "text",
      name: "question",
      value: text(item.question),
    });
    return el("form", {
      className: "add-question",
      onSubmit: function (event) {
        event.preventDefault();
        renameQuestion(item.id, field.value, labelField.value);
      },
    }, [
      labelField,
      field,
      el("div", { className: "action-row" }, [
        el("button", { className: "primary", type: "submit" }, ["记下"]),
        el("button", {
          className: "quiet",
          type: "button",
          onClick: function () {
            editingQuestionId = null;
            renderQuestions();
          },
        }, ["取消"]),
      ]),
    ]);
  }

  function splitRoundQuestions() {
    if (splittingQuestions) return;
    splittingQuestions = true;
    renderQuestions();
    writeJson("/projects/" + encodeURIComponent(projectId) + "/round-questions/draft", {})
      .then(function (payload) {
        pendingRoundQuestions = payload.questions || [];
        splittingQuestions = false;
        showFlash(payload.confirmation && payload.confirmation.message);
        renderQuestions();
      })
      .catch(function (error) {
        splittingQuestions = false;
        showFlash(explainHttpError(error), true);
        renderQuestions();
      });
  }

  function adoptRoundQuestions() {
    const questions = pendingRoundQuestions || [];
    writeJson("/projects/" + encodeURIComponent(projectId) + "/round-questions/adopt", {
      questions: questions,
    })
      .then(function (payload) {
        bench = payload.workbench;
        pendingRoundQuestions = null;
        selectedQuestionId = (payload.question_ids && payload.question_ids[0]) || null;
        showFlash(payload.confirmation && payload.confirmation.message);
        renderBench();
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function renameQuestion(questionId, question, label) {
    const next = text(question).trim();
    if (!next) {
      showFlash("本轮问题不能空着。", true);
      return;
    }
    writeJson("/research-questions/" + encodeURIComponent(questionId) + "/rename", {
      question: next,
      label: text(label).trim(),
    })
      .then(function (payload) {
        bench = payload.workbench;
        editingQuestionId = null;
        showFlash(payload.confirmation && payload.confirmation.message);
        renderBench();
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function addQuestionControls() {
    if (!addingQuestion) {
      return el("div", { className: "add-line" }, [
        el("button", {
          className: "ghost",
          type: "button",
          onClick: function () {
            addingQuestion = true;
            renderQuestions();
          },
          title: "在这一轮里再加一条要回答的问题",
        }, ["＋ 再加一条问题"]),
      ]);
    }
    const field = el("input", {
      type: "text",
      name: "question",
      placeholder: "这轮还要回答什么",
    });
    return el("form", {
      className: "add-question",
      onSubmit: function (event) {
        event.preventDefault();
        addQuestion(field.value);
      },
    }, [
      field,
      el("div", { className: "action-row" }, [
        el("button", { className: "primary", type: "submit" }, ["加上"]),
        el("button", {
          className: "quiet",
          type: "button",
          onClick: function () {
            addingQuestion = false;
            renderQuestions();
          },
        }, ["取消"]),
      ]),
    ]);
  }

  function addQuestion(question) {
    const next = text(question).trim();
    if (!next) {
      showFlash("本轮问题不能空着。", true);
      return;
    }
    writeJson("/projects/" + encodeURIComponent(projectId) + "/research-questions", {
      question: next,
    })
      .then(function (payload) {
        bench = payload.workbench;
        addingQuestion = false;
        selectedQuestionId = payload.question_id || selectedQuestionId;
        showFlash(payload.confirmation && payload.confirmation.message);
        renderBench();
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function deferQuestion(questionId) {
    writeJson("/research-questions/" + encodeURIComponent(questionId) + "/defer", {})
      .then(function (payload) {
        bench = payload.workbench;
        if (selectedQuestionId === questionId) selectedQuestionId = null;
        showFlash(payload.confirmation && payload.confirmation.message);
        renderBench();
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function restoreQuestion(questionId) {
    writeJson("/research-questions/" + encodeURIComponent(questionId) + "/restore", {})
      .then(function (payload) {
        bench = payload.workbench;
        selectedQuestionId = payload.question_id || questionId;
        showFlash(payload.confirmation && payload.confirmation.message);
        renderBench();
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  const PROGRESS_CYCLE = [
    ["unwritten", "还没写"],
    ["draft", "草稿"],
    ["enough", "这轮够用了"],
  ];

  function progressControl(item) {
    const current = PROGRESS_CYCLE.find(function (pair) {
      return pair[0] === item.progress;
    }) || PROGRESS_CYCLE[0];
    const next = PROGRESS_CYCLE[
      (PROGRESS_CYCLE.indexOf(current) + 1) % PROGRESS_CYCLE.length
    ];
    return el("button", {
      className: "progress current",
      type: "button",
      title: "点一下换成" + next[1],
      "aria-label": current[1] + "，点一下换成" + next[1],
      onClick: function (event) {
        event.stopPropagation();
        selectedQuestionId = item.id;
        writeJson("/research-questions/" + encodeURIComponent(item.id) + "/progress", {
          progress: next[0],
        })
          .then(function (payload) {
            bench = payload.workbench;
            showFlash(payload.confirmation && payload.confirmation.message);
            renderBench();
          })
          .catch(function (error) {
            showFlash(explainHttpError(error), true);
          });
      },
    }, [current[1], " ▾"]);
  }

  function renderDraft() {
    if (lookbackRound) {
      renderLookbackDraft();
      return;
    }
    const blocks = (bench && bench.blocks) || [];
    const nodes = [draftFoldControl(blocks)];
    // 收口过的轮次先按文件夹叠在上面，默认收起；下面才是这一轮在写的稿。
    const folders = archivedDraftFolders();
    nodes.push.apply(nodes, folders);
    if (folders.length) {
      nodes.push(
        el("p", { className: "round-divider" }, [
          text("第 " + ((bench && bench.current_round) || 1) + " 轮（在写）"),
        ])
      );
    }
    blocks.forEach(function (block) {
      nodes.push(renderBlock(block, selectedBlockId === block.id));
    });
    if (!blocks.length) {
      nodes.push(el("p", { className: "empty" }, ["这篇稿还没有节。"]));
    }
    nodes.push(addSectionControls());
    draftRoot.replaceChildren.apply(draftRoot, keepNodes(nodes));
  }

  // 每一轮收下的稿是一个文件夹。段落对象只有一套，这里读的是那一轮收下的版本，
  // 不复制第二套结论，也不能在这里改。
  function archivedDraftFolders() {
    const rounds = (bench && bench.archived_rounds) || [];
    const nodes = [];
    rounds.forEach(function (round) {
      const sections = round.sections || [];
      if (!sections.length) return;
      const key = "draft:round:" + round.round_index;
      nodes.push(
        groupHeading(key, round.round_label + "收下的稿（只读）", sections.length, {
          defaultFolded: true,
          className: "round-folder",
        })
      );
      if (groupFolded(key, true)) return;
      nodes.push(
        el("div", { className: "round-folder-body" }, sections.map(function (section) {
          return archivedSectionCard(round, section);
        }))
      );
    });
    return nodes;
  }

  function archivedSectionCard(round, section) {
    const key = "draft:sec:" + round.round_index + ":" + section.id;
    const folded = groupFolded(key, true);
    return el("div", { className: "block archived" }, [
      el("button", {
        className: "block-title",
        type: "button",
        title: text(section.title),
        onClick: function () {
          toggleGroup(key, true);
          renderDraft();
        },
      }, [(folded ? "▸ " : "▾ ") + text(section.title)]),
      el("p", { className: "hint" }, [
        text(round.round_label + "收下的第 " + section.version + " 版"),
      ]),
      folded
        ? null
        : el("div", { className: "block-body" }, paragraphSpans(section.text).map(
            function (chunk) {
              return el("p", {}, [chunk.text]);
            }
          )),
    ]);
  }

  function renderBlock(block, selected) {
    const checks = visibleChecks(block);
    const previewing = showingPreview(block);
    const children = [blockHeading(block, selected)];
    if (!selected && !previewing && !expandAllBlocks) {
      children.push(blockGist(block));
      return el("div", {
        className: "block folded"
          + (checks.stale || block.material_since_draft ? " stale" : ""),
        onClick: function () {
          editingTitleBlockId = null;
          sectionMoreOpen = false;
          selectedExcerptClaimId = null;
          selectedBlockId = block.id;
          unsourcedNote = null;
          renderDraft();
          renderMaterials();
        },
      }, children);
    }
    if (previewing) children.push(previewBanner(block));
    children.push(markedBody(block));
    if (previewing && comparingBlockId === block.id) {
      children.push(priorBody(block));
    }
    if (selected && !previewing && block.material_since_draft) {
      children.push(
        el("p", { className: "note" }, [
          "这一节收下之后又挂了 "
            + block.material_since_draft
            + " 条原话，可能该按材料再写一版。",
        ])
      );
    }
    if (selected && checks.stale) {
      children.push(el("p", { className: "note" }, ["补料后，这一节可能过时。"]));
    }
    if (selected && (checks.novel_claims || []).length) {
      children.push(el("p", { className: "note" }, ["有未挂来源的说法，仍可收下。"]));
    }
    if (selected && (checks.client_as_verified || []).length) {
      children.push(checkNote("有客户口头被写成已核实。", checks.client_as_verified));
    }
    if (selected && (checks.feedback_as_evidence || []).length) {
      children.push(
        checkNote("有经理反馈被当成外部依据。", checks.feedback_as_evidence)
      );
    }
    if (selected && (checks.macro_as_demand || []).length) {
      children.push(checkNote("宏观材料被用来证明项目需求。", checks.macro_as_demand));
    }
    // 重复只提示、不拦截：稿照样能收，人自己决定要不要让模型重写一版。
    (selected ? checks.repeated_phrases || [] : []).forEach(function (item) {
      children.push(
        el("p", { className: "note" }, [
          text(item.text) + "。" + text(item.hint) + "。",
          (item.samples || []).length
            ? el("span", { className: "why" }, ["　例：" + item.samples.join("／")])
            : null,
        ])
      );
    });
    if (selected) children.push(numberManifest(block));
    if (selected) children.push(impactPanel(block));
    if (selected) children.push(blockExcerpts(block));
    if (selected) children.push(selectedActions(block));
    return el("div", {
      className: "block"
        + (selected ? " current" : "")
        + (checks.stale || block.material_since_draft ? " stale" : ""),
      onClick: function () {
        if (editingTitleBlockId && editingTitleBlockId !== block.id) {
          editingTitleBlockId = null;
        }
        if (selectedBlockId !== block.id) {
          sectionMoreOpen = false;
          selectedExcerptClaimId = null;
          selectedBlockId = block.id;
        } else {
          // 再点一次收起自己。原来只能靠点别的一节才收得回来
          // （流水账第 5 条）。
          selectedBlockId = null;
          sectionMoreOpen = false;
          selectedExcerptClaimId = null;
          excerptsOpenBlockId = null;
        }
        unsourcedNote = null;
        renderDraft();
        renderMaterials();
      },
    }, children);
  }



  function lookbackData() {
    const rounds = (bench && bench.archived_rounds) || [];
    for (let index = 0; index < rounds.length; index += 1) {
      if (rounds[index].round_index === lookbackRound) return rounds[index];
    }
    return null;
  }

  // 回看是只读的：那一轮收下的正文照原样铺出来，不给写、不给收下。
  function renderLookbackDraft() {
    const round = lookbackData();
    const sections = (round && round.sections) || [];
    const nodes = [
      el("div", { className: "lookback-banner" }, [
        el("span", { className: "hint" }, [
          text(
            (round ? round.round_label : "上一轮")
              + "收下的稿。只读回看，要改回到当前轮。"
          ),
        ]),
        el("button", {
          className: "ghost",
          type: "button",
          onClick: function () {
            lookbackRound = null;
            selectedQuestionId = null;
            renderBench();
          },
        }, ["回到当前轮"]),
      ]),
    ];
    if (!sections.length) {
      nodes.push(el("p", { className: "empty" }, ["这一轮没有收下过正文。"]));
    }
    sections.forEach(function (section) {
      nodes.push(
        el("div", { className: "block archived" }, [
          el("strong", {}, [text(section.title)]),
          el("p", { className: "hint" }, [
            text((round ? round.round_label : "") + "收下的第 " + section.version + " 版"),
          ]),
          el("div", { className: "block-body" }, paragraphSpans(section.text).map(
            function (chunk) {
              return el("p", {}, [chunk.text]);
            }
          )),
        ])
      );
    });
    draftRoot.replaceChildren.apply(draftRoot, keepNodes(nodes));
  }

  function draftFoldControl(blocks) {
    if (blocks.length < 2) return null;
    return el("div", { className: "fold-all" }, [
      el("button", {
        className: "linky",
        type: "button",
        onClick: function () {
          expandAllBlocks = !expandAllBlocks;
          renderDraft();
        },
      }, [expandAllBlocks ? "只看选中的一节" : "全部展开"]),
    ]);
  }

  // 没选中的节只给一行：标题在上面，这里说这节现在什么样。
  function blockGist(block) {
    const marks = [];
    if (block.placeholder) marks.push("还没写");
    if (block.material_since_draft) {
      marks.push("新挂了 " + block.material_since_draft + " 条原话");
    }
    const checks = visibleChecks(block);
    const red = (checks.unsourced_marks || []).length;
    if (red) marks.push(red + " 处标红");
    if (checks.stale) marks.push("可能过时");
    const body = text(block.current_text).replace(/\s+/g, " ").trim();
    const gist = block.placeholder || !body
      ? ""
      : (body.length > GIST_CHARS ? body.slice(0, GIST_CHARS) + "…" : body);
    return el("div", { className: "block-gist" }, [
      gist ? el("p", { className: "gist-line", title: text(block.current_text) }, [gist]) : null,
      marks.length ? el("p", { className: "hint" }, [marks.join(" · ")]) : null,
    ]);
  }

  function blockHeading(block, selected) {
    if (selected && editingTitleBlockId === block.id) {
      const field = el("input", {
        type: "text",
        value: text(block.title),
        onClick: stopBubble,
      });
      queueMicrotask(function () {
        field.focus();
        field.select();
      });
      return el("div", { className: "block-title-edit", onClick: stopBubble }, [
        field,
        el("div", { className: "action-row" }, [
          el("button", {
            className: "primary",
            type: "button",
            onClick: function () {
              saveTitle(block.id, field.value);
            },
          }, ["记下"]),
          el("button", {
            className: "quiet",
            type: "button",
            onClick: function () {
              editingTitleBlockId = null;
              renderDraft();
            },
          }, ["取消"]),
        ]),
      ]);
    }
    if (selected) {
      return el("button", {
        className: "block-title",
        type: "button",
        onClick: function (event) {
          event.stopPropagation();
          editingTitleBlockId = block.id;
          renderDraft();
        },
      }, [text(block.title)]);
    }
    return el("strong", {}, [text(block.title)]);
  }

  function saveTitle(blockId, value) {
    const next = text(value).trim();
    if (!next) {
      showFlash("节名不能空着。", true);
      return;
    }
    writeJson("/deliverable-blocks/" + encodeURIComponent(blockId) + "/title", {
      title: next,
    })
      .then(function (payload) {
        editingTitleBlockId = null;
        selectedBlockId = blockId;
        const message = payload.confirmation && payload.confirmation.message;
        if (payload.workbench) {
          bench = payload.workbench;
          renderBench();
          showFlash(message);
          return;
        }
        return loadBench().then(function () {
          showFlash(message);
        });
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function showingPreview(block) {
    return !!(
      selectedBlockId === block.id
      && block.pending_revision
      && !dismissedPreview[block.id]
    );
  }

  function visibleChecks(block) {
    if (showingPreview(block) && block.preview_checks) return block.preview_checks;
    return block.checks || {};
  }

  function checkNote(label, items) {
    return el("p", {
      className: "note",
      onClick: function (event) {
        event.stopPropagation();
        const sourceId = items[0] && items[0].source_id;
        if (sourceId) {
          highlightSourceId = sourceId;
          selectedMaterialId = sourceId;
          unsourcedNote = null;
        }
        renderMaterials();
      },
    }, [label]);
  }

  function markedBody(block) {
    const checks = visibleChecks(block);
    const marks = (checks.unsourced_marks && checks.unsourced_marks.length)
      ? checks.unsourced_marks
      : (checks.unsourced_numbers || []).concat(checks.unsourced_orgs || []);
    const source = showingPreview(block)
      ? text(block.pending_revision && block.pending_revision.body)
      : text(block.current_text);
    if (!source || source === PLACEHOLDER_TEXT) {
      return el("p", { className: "empty" }, [PLACEHOLDER_TEXT]);
    }
    const chunks = paragraphSpans(source);
    return el("div", { className: "block-body" }, chunks.map(function (chunk) {
      return markedParagraph(block, chunk.text, chunk.start, marks);
    }));
  }

  function paragraphSpans(source) {
    const raw = String(source || "").replace(/\r\n/g, "\n");
    if (!raw.trim()) return [];
    if (raw.indexOf("\n") !== -1) {
      const rows = [];
      let start = 0;
      for (let index = 0; index < raw.length; index += 1) {
        if (raw.charAt(index) !== "\n") continue;
        const piece = raw.slice(start, index);
        if (piece.trim()) rows.push({ text: piece, start: start });
        start = index + 1;
      }
      const tail = raw.slice(start);
      if (tail.trim()) rows.push({ text: tail, start: start });
      return rows;
    }
    const parts = [];
    let last = 0;
    for (let index = 0; index < raw.length; index += 1) {
      const ch = raw.charAt(index);
      if (ch !== "。" && ch !== "！" && ch !== "？") continue;
      const piece = raw.slice(last, index + 1);
      if (piece.trim()) parts.push({ text: piece, start: last });
      last = index + 1;
    }
    const tail = raw.slice(last);
    if (tail.trim()) parts.push({ text: tail, start: last });
    if (parts.length <= 1) return [{ text: raw.trim(), start: 0 }];
    if (parts.length <= 4) return parts;
    const grouped = [];
    for (let index = 0; index < parts.length; index += 2) {
      const chunk = parts.slice(index, index + 2);
      grouped.push({
        text: chunk.map(function (item) { return item.text; }).join(""),
        start: chunk[0].start,
      });
    }
    return grouped;
  }

  function markedParagraph(block, source, origin, marks) {
    const local = (marks || []).filter(function (mark) {
      return mark.start >= origin && mark.end <= origin + source.length;
    }).map(function (mark) {
      return {
        text: mark.text,
        start: mark.start - origin,
        end: mark.end - origin,
        source_id: mark.source_id,
      };
    });
    if (!local.length) return el("p", {}, [source]);
    const parts = [];
    let cursor = 0;
    local.forEach(function (mark) {
      if (mark.start > cursor) parts.push(source.slice(cursor, mark.start));
      parts.push(
        el("span", {
          className: "marked",
          title: "未挂来源",
          onClick: function (event) {
            event.stopPropagation();
            highlightSourceId = mark.source_id || null;
            selectedMaterialId = mark.source_id || null;
            unsourcedNote = mark.source_id ? null : mark.text;
            selectedBlockId = block.id;
            renderDraft();
            renderMaterials();
          },
        }, [source.slice(mark.start, mark.end)])
      );
      cursor = mark.end;
    });
    if (cursor < source.length) parts.push(source.slice(cursor));
    return el("p", {}, parts);
  }

  function stopBubble(event) {
    event.stopPropagation();
  }

  // 稿里每个数字逐个对出处。模型编数字比编论述隐蔽得多——一段论述读着不对
  // 人能看出来，一个「19.1亿元」混在通顺的句子里人不会怀疑。默认收起。
  function numberManifest(block) {
    const manifest = block.number_manifest || {};
    const rows = manifest.numbers || [];
    if (!rows.length) return null;
    const missing = manifest.unsourced || 0;
    const open = numbersOpenBlockId === block.id;
    const head = el("button", {
      className: "numbers-heading" + (missing ? " has-missing" : ""),
      type: "button",
      onClick: function (event) {
        event.stopPropagation();
        numbersOpenBlockId = open ? null : block.id;
        renderDraft();
      },
    }, [
      (open ? "▾ " : "▸ ") + "这一节的数字（" + manifest.total + " 个"
        + (missing ? "，" + missing + " 个找不到出处" : "，都挂得住") + "）",
    ]);
    if (!open) return el("div", { className: "numbers", onClick: stopBubble }, [head]);
    return el("div", { className: "numbers open", onClick: stopBubble }, [head].concat(
      rows.map(function (item) {
        return el("div", {
          className: "number-row" + (item.found_in_excerpt ? "" : " missing"),
        }, [
          el("span", { className: "number-token" }, [text(item.number)]),
          // 一行放不下就截断，鼠标停上去看全的：研报标题动辄四十几个字。
          el("span", {
            className: "number-context",
            title: text(item.context),
          }, [text(item.context)]),
          el("span", {
            className: "number-where",
            title: item.found_in_excerpt
              ? text(item.source_title || "已挂原话里有")
              : "找不到出处",
          }, [
            item.found_in_excerpt
              ? text(item.source_title || "已挂原话里有")
              : "找不到出处",
          ]),
        ]);
      })
    ).concat([
      el("p", { className: "why" }, [text(manifest.limitation)]),
    ]));
  }

  // 「改这一节，别的哪几节跟着要看」。三类咨询活累的都是改不是写：
  // 这一版把某个数字改了，稿里别处还照旧写着老口径，人自己记不住。
  // 这里只列已经挂着的关系，列出来不等于那几节就错了，也绝不自动去改它们。
  function impactPanel(block) {
    const impact = block.impact || {};
    const rows = impact.related || [];
    if (!rows.length) return null;
    const urgent = rows.filter(function (row) {
      return (row.reasons || []).some(function (item) {
        return item.kind === "changed_number";
      });
    });
    const open = impactOpenBlockId === block.id || urgent.length > 0;
    const head = el("button", {
      className: "impact-heading" + (urgent.length ? " has-changed" : ""),
      type: "button",
      onClick: function (event) {
        event.stopPropagation();
        impactOpenBlockId = open && !urgent.length ? null : block.id;
        renderDraft();
      },
    }, [(open ? "▾ " : "▸ ") + text(impact.heading || "")]);
    if (!open) return el("div", { className: "impact", onClick: stopBubble }, [head]);
    return el("div", { className: "impact open", onClick: stopBubble }, [head].concat(
      rows.map(function (row) {
        const changed = (row.reasons || []).filter(function (item) {
          return item.kind === "changed_number";
        });
        return el("div", {
          className: "impact-row" + (changed.length ? " changed" : ""),
        }, [
          el("button", {
            className: "impact-jump",
            type: "button",
            onClick: function (event) {
              event.stopPropagation();
              selectedBlockId = row.block_id;
              renderDraft();
            },
          }, [text(row.title)]),
          el("span", {
            className: "impact-why",
            title: (row.reasons || []).map(function (item) {
              return text(item.label) + "：" + text(item.detail);
            }).join("　"),
          }, [
            (row.reasons || []).slice(0, 4).map(function (item) {
              return text(item.label) + "：" + text(item.detail);
            }).join("　"),
          ]),
        ]);
      })
    ).concat([
      el("p", { className: "why" }, [text(impact.limitation)]),
    ]));
  }

  function blockExcerpts(block) {
    const items = (block.claim_sources || []).filter(function (item) {
      return item.excerpt;
    });
    if (!items.length) return null;
    // 原话默认收起：它是佐证不是正文，铺开会把稿淹掉（流水账第 6 条）。
    const open = excerptsOpenBlockId === block.id;
    const rows = [
      el("button", {
        className: "excerpts-heading",
        type: "button",
        onClick: function (event) {
          event.stopPropagation();
          excerptsOpenBlockId = open ? null : block.id;
          renderDraft();
        },
      }, [
        (open ? "▾ " : "▸ ") + "这一节挂着的原话（" + items.length + " 条"
          + (open ? "，点一条能翻到材料" : "") + "）",
      ]),
    ];
    if (!open) {
      return el("div", { className: "block-excerpts", onClick: stopBubble }, rows);
    }
    return el("div", { className: "block-excerpts open", onClick: stopBubble }, rows.concat(items.map(function (item) {
      const prefix = item.source_title ? item.source_title + " · " : "";
      const parts = [prefix].concat(
        excerptText("claim:" + item.claim_id, item.excerpt)
      );
      return el("div", { className: "block-excerpt" }, [
        el("p", {
          className: "excerpt" + (item.claim_id === selectedExcerptClaimId ? " current" : ""),
          onClick: function (event) {
            event.stopPropagation();
            selectedExcerptClaimId = item.claim_id === selectedExcerptClaimId ? null : item.claim_id;
            if (!item.source_id) {
              renderDraft();
              return;
            }
            highlightSourceId = item.source_id;
            selectedMaterialId = item.source_id;
            unsourcedNote = null;
            renderDraft();
            renderMaterials();
          },
        }, parts),
        item.claim_id && item.claim_id === selectedExcerptClaimId
          ? el("button", {
              className: "quiet",
              type: "button",
              onClick: function (event) {
                event.stopPropagation();
                unlinkExcerpt(block.id, item.claim_id);
              },
            }, ["这节先不用"])
          : null,
      ]);
    })));
  }

  function unlinkExcerpt(blockId, claimId) {
    writeJson(
      "/deliverable-blocks/" + encodeURIComponent(blockId)
        + "/claims/" + encodeURIComponent(claimId) + "/unlink",
      {}
    )
      .then(function (result) {
        selectedBlockId = blockId;
        selectedExcerptClaimId = null;
        const message = result.confirmation && result.confirmation.message;
        if (result.workbench) {
          bench = result.workbench;
          renderBench();
          showFlash(message);
          return;
        }
        return loadBench().then(function () {
          showFlash(message);
        });
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }


  function previewBanner(block) {
    const prior = text(block.current_text).trim();
    const open = comparingBlockId === block.id;
    return el("div", { className: "preview-banner", onClick: stopBubble }, [
      el("span", { className: "hint" }, ["下面是模型写的这一版，还没收下。"]),
      prior && prior !== PLACEHOLDER_TEXT
        ? el("button", {
            className: "ghost",
            type: "button",
            onClick: function () {
              comparingBlockId = open ? null : block.id;
              renderDraft();
            },
          }, [open ? "收起上一版" : "对一下上一版"])
        : null,
    ]);
  }

  function priorBody(block) {
    const prior = text(block.current_text).trim();
    if (!prior || prior === PLACEHOLDER_TEXT) return null;
    return el("div", { className: "prior-body", onClick: stopBubble }, [
      el("p", { className: "hint" }, ["上一版（现在稿上的这一节）"]),
      el("div", { className: "block-body" }, paragraphSpans(prior).map(function (chunk) {
        return el("p", {}, [chunk.text]);
      })),
    ]);
  }

  // 人粘进来的原话可能是几千字。库里一个字不动，只是先给个开头，点开才铺全。
  function excerptText(key, body) {
    const full = text(body);
    const open = expandedExcerpts[key];
    if (full.length <= EXCERPT_PREVIEW_CHARS) return [full];
    return [
      open ? full : full.slice(0, EXCERPT_PREVIEW_CHARS) + "…",
      el("button", {
        className: "linky",
        type: "button",
        onClick: function (event) {
          event.stopPropagation();
          if (open) delete expandedExcerpts[key];
          else expandedExcerpts[key] = true;
          renderDraft();
          renderMaterials();
        },
      }, [open ? "收起" : "看全文（" + full.length + " 字）"]),
    ];
  }

  function selectedActions(block) {
    const pending = block.pending_revision;
    const showPreview = pending && !dismissedPreview[block.id];
    if (showPreview) {
      return revisionEditor(block, pending.body, "模型写了这一节候选。收下后才进给经理的稿。", "pending");
    }
    if (editingBlockId === block.id) {
      return revisionEditor(block, block.current_text, "改完收下才换这一节。", "edit");
    }
    const busy = writingBlockId === block.id;
    const buttons = [
      el("button", {
        className: "primary",
        type: "button",
        disabled: busy,
        onClick: function () {
          writeSection(block.id);
        },
      }, [busy ? "正在写…" : writeButtonLabel(block)]),
      el("button", {
        className: "ghost",
        type: "button",
        disabled: busy,
        onClick: function () {
          editingBlockId = block.id;
          renderDraft();
        },
      }, ["改这一节"]),
    ];
    const extra = [];
    if (block.can_remove) {
      buttons.push(
        el("button", {
          className: "ghost",
          type: "button",
          disabled: busy,
          onClick: function () {
            removeSection(block.id);
          },
        }, ["去掉这一节"])
      );
    }
    if (block.prior_revision && text(block.prior_revision.body).trim()) {
      extra.push(
        el("button", {
          className: "ghost",
          type: "button",
          disabled: busy,
          onClick: function () {
            restorePrior(block);
          },
        }, ["收回上一版"])
      );
    }
    if (extra.length) {
      buttons.push(
        el("button", {
          className: "quiet",
          type: "button",
          disabled: busy,
          onClick: function () {
            sectionMoreOpen = !sectionMoreOpen;
            renderDraft();
          },
        }, [sectionMoreOpen ? "收起" : "更多"])
      );
    }
    const row = el("div", { className: "action-row", onClick: stopBubble }, buttons);
    if (sectionMoreOpen && extra.length) {
      return el("div", { onClick: stopBubble }, [
        row,
        el("div", { className: "action-row" }, extra),
      ]);
    }
    return row;
  }

  function revisionEditor(block, body, note, mode) {
    const area = el("textarea", { rows: "8" }, [text(body)]);
    return el("div", { className: "preview", onClick: stopBubble }, [
      note ? el("p", { className: "hint" }, [note]) : null,
      area,
      el("div", { className: "action-row" }, [
        el("button", {
          className: "primary",
          type: "button",
          onClick: function () {
            acceptPreview(block, area.value);
          },
        }, ["收下"]),
        el("button", {
          className: "quiet",
          type: "button",
          onClick: function () {
            if (mode === "edit") editingBlockId = null;
            else dismissedPreview[block.id] = true;
            renderDraft();
          },
        }, ["丢掉"]),
      ]),
    ]);
  }

  function writeButtonLabel(block) {
    return block && !block.placeholder ? "按材料再写一版" : "按材料写这一节";
  }

  function sectionHasHungExcerpt(block) {
    return ((block && block.claim_sources) || []).some(function (item) {
      return text(item.excerpt).trim();
    });
  }

  function writeSection(blockId) {
    if (writingBlockId) return;
    const block = ((bench && bench.blocks) || []).filter(function (item) {
      return item.id === blockId;
    })[0];
    const hung = sectionHasHungExcerpt(block);
    const questions = (bench && bench.questions) || [];
    if (questions.length && !selectedQuestionId && !hung) {
      showFlash("先点开左边要回答的那条，从快照挂上原话，再按材料写这一节。", true);
      return;
    }
    if (!hung) {
      showFlash("先从右边快照扒原话并收下，再按材料写这一节。", true);
      return;
    }
    writingBlockId = blockId;
    const payload = {};
    if (selectedQuestionId) payload.question_id = selectedQuestionId;
    showFlash(
      selectedQuestionId
        ? "将发送：点开的问题、这一节已挂原话。正在按材料写…"
        : "将发送：这一节已挂原话。正在按材料写…"
    );
    renderDraft();
    writeJson("/deliverable-blocks/" + encodeURIComponent(blockId) + "/draft-revision", payload)
      .then(function (payload) {
        dismissedPreview[blockId] = false;
        editingBlockId = null;
        showFlash(payload.confirmation && payload.confirmation.message);
        return loadBench();
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      })
      .then(function () {
        writingBlockId = null;
        renderDraft();
      });
  }

  function restorePrior(block) {
    const body = text(block.prior_revision && block.prior_revision.body).trim();
    if (!body) {
      showFlash("这一节还没有上一版。", true);
      return;
    }
    writeJson("/deliverable-blocks/" + encodeURIComponent(block.id) + "/revisions", {
      body: body,
    })
      .then(function () {
        dismissedPreview[block.id] = false;
        editingBlockId = null;
        showFlash("上一版已作为候选。收下后才换回去。");
        return loadBench();
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function acceptPreview(block, body) {
    const pending = block.pending_revision;
    const next = (body || "").trim();
    const current = text(block.current_text).trim();
    if (!next) {
      showFlash("这一节不能空着。", true);
      return;
    }
    if (!pending && next === current) {
      editingBlockId = null;
      showFlash("这一节没有改动。");
      renderDraft();
      return;
    }
    const chain = pending && next === text(pending.body).trim()
      ? Promise.resolve(pending.version)
      : writeJson("/deliverable-blocks/" + encodeURIComponent(block.id) + "/revisions", {
          body: next,
        }).then(function (payload) {
          return payload.pending_revision.version;
        });
    chain
      .then(function (version) {
        return writeJson(
          "/deliverable-blocks/" + encodeURIComponent(block.id) + "/revisions/adopt",
          { version: version }
        );
      })
      .then(function () {
        dismissedPreview[block.id] = true;
        editingBlockId = null;
        showFlash("已收下，进给经理的稿。");
        return bumpQuestionDraft().then(loadBench);
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function bumpQuestionDraft() {
    if (!selectedQuestionId || !bench) return Promise.resolve();
    const item = ((bench.questions || []).filter(function (row) {
      return row.id === selectedQuestionId;
    })[0]);
    if (!item || item.progress !== "unwritten") return Promise.resolve();
    return writeJson(
      "/research-questions/" + encodeURIComponent(selectedQuestionId) + "/progress",
      { progress: "draft" }
    ).then(function (payload) {
      if (payload.workbench) bench = payload.workbench;
    });
  }

  function removeSection(blockId) {
    writeDelete("/deliverable-blocks/" + encodeURIComponent(blockId))
      .then(function (payload) {
        if (selectedBlockId === blockId) selectedBlockId = null;
        showFlash(payload.confirmation && payload.confirmation.message);
        return loadBench();
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function addSectionControls() {
    if (addingSection) {
      const field = el("input", {
        type: "text",
        placeholder: "这一节叫什么",
        onClick: stopBubble,
      });
      queueMicrotask(function () {
        field.focus();
      });
      return el("div", { className: "add-line", onClick: stopBubble }, [
        field,
        el("div", { className: "action-row" }, [
          el("button", {
            className: "primary",
            type: "button",
            onClick: function () {
              addSection(field.value);
            },
          }, ["加上"]),
          el("button", {
            className: "quiet",
            type: "button",
            onClick: function () {
              addingSection = false;
              renderDraft();
            },
          }, ["取消"]),
        ]),
      ]);
    }
    return el("div", { className: "add-line" }, [
      el("button", {
        className: "ghost",
        type: "button",
        onClick: function () {
          addingSection = true;
          renderDraft();
        },
      }, ["加一节"]),
    ]);
  }

  function addSection(title) {
    const next = text(title).trim();
    if (!next) {
      showFlash("节名不能空着。", true);
      return;
    }
    writeJson("/projects/" + encodeURIComponent(projectId) + "/deliverable-blocks", {
      title: next,
      current_text: PLACEHOLDER_TEXT,
    })
      .then(function (payload) {
        addingSection = false;
        selectedBlockId = payload.block_id || (payload.report && payload.report.blocks.slice(-1)[0].id);
        editingTitleBlockId = null;
        return loadBench();
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }


  function readCollapsed() {
    try {
      return JSON.parse(localStorage.getItem("jingwei-bench-folds") || "{}") || {};
    } catch (error) {
      return {};
    }
  }

  // 收口过的轮次默认是收起的：本轮的东西该在眼前，旧轮只在需要时点开。
  function groupFolded(key, defaultFolded) {
    if (collapsedGroups[key] === undefined) return !!defaultFolded;
    return !!collapsedGroups[key];
  }

  function toggleGroup(key, defaultFolded) {
    if (groupFolded(key, defaultFolded)) collapsedGroups[key] = false;
    else collapsedGroups[key] = true;
    try {
      localStorage.setItem("jingwei-bench-folds", JSON.stringify(collapsedGroups));
    } catch (error) {
      // 存不下就只在这次会话里记着，不影响账本
    }
  }

  // 标题不再一律截断：自己起过短名就用短名，没起就把整句写全。
  // 无论哪种，光标停上去都能看到整句。
  // replaceChildren 会把 null 当成文字「null」塞进页面。空白题上这条一直在漏，
  // 新建题目的左栏底下会凭空多出两个 null。渲染前统一滤掉。
  function keepNodes(nodes) {
    return (nodes || []).filter(function (node) {
      return node !== null && node !== undefined && node !== false;
    });
  }

  function questionTitle(item) {
    const full = text(item.question || "");
    const label = text(item.label || "").trim();
    return label || full;
  }

  function groupHeading(key, label, count, options) {
    const config = options || {};
    const folded = groupFolded(key, config.defaultFolded);
    return el("button", {
      className: "group-heading"
        + (folded ? " folded" : "")
        + (config.className ? " " + config.className : ""),
      type: "button",
      onClick: function (event) {
        event.stopPropagation();
        toggleGroup(key, config.defaultFolded);
        renderMaterials();
        renderQuestions();
        renderDraft();
      },
      title: text(label) + (folded ? "（点开这组）" : "（收起这组）"),
    }, [
      el("span", { className: "fold-mark" }, [folded ? "▸" : "▾"]),
      el("span", { className: "fold-label", title: text(label) }, [text(label)]),
      el("span", { className: "fold-count" }, [String(count)]),
    ]);
  }

  function renderLookbackMaterials() {
    const round = lookbackData();
    const ids = {};
    ((round && round.questions) || []).forEach(function (item) {
      ids[item.id] = true;
    });
    const materials = (bench && bench.materials) || {};
    const pick = function (rows) {
      return (rows || []).filter(function (item) {
        if (!ids[item.research_question_id]) return false;
        if (!selectedQuestionId) return true;
        return item.research_question_id === selectedQuestionId;
      });
    };
    const sources = pick(materials.sources);
    const candidates = pick(materials.candidates);
    const nodes = [
      el("p", { className: "hint" }, [
        text((round ? round.round_label : "上一轮") + "用过的材料。只读回看。"),
      ]),
    ];
    if (!sources.length && !candidates.length) {
      nodes.push(el("p", { className: "empty" }, [
        selectedQuestionId ? "这条问题当时没有挂上材料。" : "这一轮没有标到问题上的材料。",
      ]));
    }
    sources.forEach(function (item) {
      nodes.push(sourceCard(item, false));
    });
    candidates.forEach(function (item) {
      nodes.push(candidateCard(item, false));
    });
    materialsRoot.replaceChildren.apply(materialsRoot, keepNodes(nodes));
  }

  function renderMaterials() {
    if (lookbackRound) {
      renderLookbackMaterials();
      return;
    }
    const materials = (bench && bench.materials) || {};
    const allSources = materials.sources || [];
    const allCandidates = materials.candidates || [];
    const nodes = [addMaterialControls()];
    if (unsourcedNote) {
      nodes.push(el("p", { className: "note" }, ["「" + unsourcedNote + "」未挂来源"]));
    }
    const groups = materialGroups(allSources, allCandidates);
    if (!groups.length && !unsourcedNote) {
      nodes.push(el("p", { className: "empty" }, [
        selectedQuestionId ? "这条还没有材料。按这条问题搜，打开后从快照扒原话。" : "还没有材料。",
      ]));
      materialsRoot.replaceChildren.apply(materialsRoot, keepNodes(nodes));
      return;
    }
    // 本轮的分组摊开在上面；收口过的轮次各收进一个「第 N 轮」总文件夹。
    const live = groups.filter(function (group) {
      return !group.round_index;
    });
    const byRound = [];
    groups.forEach(function (group) {
      if (!group.round_index) return;
      let bucket = byRound.filter(function (item) {
        return item.round_index === group.round_index;
      })[0];
      if (!bucket) {
        bucket = {
          round_index: group.round_index,
          round_label: group.round_label,
          groups: [],
        };
        byRound.push(bucket);
      }
      bucket.groups.push(group);
    });
    byRound.sort(function (a, b) {
      return a.round_index - b.round_index;
    });

    function pushGroup(target, group, index) {
      const key = "mat:" + (group.key || index);
      const count = group.sources.length + group.candidates.length;
      target.push(groupHeading(key, group.heading, count));
      if (groupFolded(key)) return;
      if (group.untagged) {
        const bulk = bulkAssignRow();
        if (bulk) target.push(bulk);
      }
      group.sources.forEach(function (item) {
        target.push(sourceCard(item, group.untagged));
      });
      group.candidates.forEach(function (item) {
        target.push(candidateCard(item, group.untagged));
      });
    }

    byRound.forEach(function (bucket) {
      const key = "mat:round:" + bucket.round_index;
      const count = bucket.groups.reduce(function (sum, group) {
        return sum + group.sources.length + group.candidates.length;
      }, 0);
      nodes.push(
        groupHeading(key, bucket.round_label + "用过的材料", count, {
          defaultFolded: true,
          className: "round-folder",
        })
      );
      if (groupFolded(key, true)) return;
      const inner = [];
      bucket.groups.forEach(function (group, index) {
        pushGroup(inner, group, index);
      });
      nodes.push(el("div", { className: "round-folder-body" }, inner));
    });

    live.forEach(function (group, index) {
      pushGroup(nodes, group, index);
    });
    const aside = (materials.set_aside || []);
    if (aside.length) {
      nodes.push(groupHeading("mat:set-aside", "这轮不用的", aside.length));
      if (!groupFolded("mat:set-aside")) {
        aside.forEach(function (item) {
          nodes.push(setAsideCard(item));
        });
      }
    }
    materialsRoot.replaceChildren.apply(materialsRoot, keepNodes(nodes));
  }

  function materialGroups(allSources, allCandidates) {
    const questions = (bench && bench.questions) || [];
    const labels = {};
    questions.forEach(function (item) {
      // 跟左栏同一个规则：自己起过短名就用短名，没起就写整句
      labels[item.id] = questionTitle(item);
    });
    // 上一轮的问题也要认。后端一直标着 research_question_id，界面若只认本轮，
    // 收口开下一轮之后所有旧材料都会显示成「还没标对应哪条问题」——标好了却看不出来。
    const archivedLabels = {};
    // 轮号不再写进每条分组的标题里：同一轮的分组会收进一个「第 N 轮」总文件夹，
    // 标题只留问题本身，省得一屏全是「第 1 轮 · 」。
    const archivedRoundOf = {};
    ((bench && bench.archived_rounds) || []).forEach(function (round) {
      (round.questions || []).forEach(function (item) {
        archivedLabels[item.id] = questionTitle(item);
        archivedRoundOf[item.id] = round;
      });
    });
    ((bench && bench.deferred_questions) || []).forEach(function (item) {
      if (!labels[item.id] && !archivedLabels[item.id]) {
        archivedLabels[item.id] = "这轮先不用 · " + questionTitle(item);
      }
    });
    function labelFor(id) {
      if (!id) return "还没标对应哪条问题";
      return labels[id] || archivedLabels[id] || "还没标对应哪条问题";
    }
    if (selectedQuestionId) {
      const current = questions.filter(function (item) {
        return item.id === selectedQuestionId;
      })[0];
      const heading = current ? "对应「" + questionTitle(current) + "」" : "这条问题的材料";
      const taggedSources = allSources.filter(function (item) {
        return item.research_question_id === selectedQuestionId;
      });
      const taggedCandidates = allCandidates.filter(function (item) {
        return item.research_question_id === selectedQuestionId;
      });
      const untaggedSources = allSources.filter(function (item) {
        return !item.research_question_id;
      });
      const untaggedCandidates = allCandidates.filter(function (item) {
        return !item.research_question_id;
      });
      const groups = [];
      if (taggedSources.length || taggedCandidates.length) {
        groups.push({
          key: selectedQuestionId,
          heading: heading,
          sources: taggedSources,
          candidates: taggedCandidates,
          untagged: false,
        });
      }
      if (untaggedSources.length || untaggedCandidates.length) {
        groups.push({
          key: "untagged",
          heading: "还没标对应哪条问题",
          sources: untaggedSources,
          candidates: untaggedCandidates,
          untagged: true,
        });
      }
      return groups;
    }
    const order = [];
    const buckets = {};
    function bucket(id, fallback) {
      const key = id || "";
      if (!buckets[key]) {
        // 标题优先用 labelFor：本轮问题给原话，上一轮的带上「第 N 轮 · 」，
        // 这样收口之后也看得出这份材料当初是回答哪一轮的哪条。
        const known = key ? labelFor(id) : "还没标对应哪条问题";
        const round = id ? archivedRoundOf[id] : null;
        buckets[key] = {
          key: key || "untagged",
          heading: key && known === "还没标对应哪条问题"
            ? (fallback || known)
            : known,
          sources: [],
          candidates: [],
          untagged: !id,
          round_index: round ? round.round_index : null,
          round_label: round ? round.round_label : null,
        };
        order.push(key);
      }
      return buckets[key];
    }
    questions.forEach(function (item) {
      bucket(item.id, item.question);
    });
    allSources.forEach(function (item) {
      bucket(item.research_question_id, item.question_label).sources.push(item);
    });
    allCandidates.forEach(function (item) {
      bucket(item.research_question_id, item.question_label).candidates.push(item);
    });
    const untagged = buckets[""];
    const groups = order.filter(function (key) {
      if (!key) return false;
      const group = buckets[key];
      return group.sources.length || group.candidates.length;
    }).map(function (key) {
      return buckets[key];
    });
    if (untagged && (untagged.sources.length || untagged.candidates.length)) {
      groups.push(untagged);
    }
    return groups;
  }


  function bulkPickBox(item, kind) {
    if (!selectedQuestionId || lookbackRound) return null;
    const checked = bulkPicks[item.id] === kind;
    return el("label", { className: "pick", onClick: stopBubble }, [
      el("input", {
        type: "checkbox",
        checked: checked,
        onChange: function (event) {
          if (event.target.checked) bulkPicks[item.id] = kind;
          else delete bulkPicks[item.id];
          renderMaterials();
        },
      }),
      "一起归",
    ]);
  }

  function bulkAssignRow() {
    if (!selectedQuestionId) return null;
    const ids = Object.keys(bulkPicks);
    if (!ids.length) return null;
    return el("div", { className: "action-row", onClick: stopBubble }, [
      el("button", {
        className: "ghost",
        type: "button",
        onClick: function () {
          const sourceIds = ids.filter(function (id) {
            return bulkPicks[id] === "source";
          });
          const candidateIds = ids.filter(function (id) {
            return bulkPicks[id] === "candidate";
          });
          writeJson(
            "/projects/" + encodeURIComponent(projectId) + "/materials/question",
            {
              question_id: selectedQuestionId,
              source_ids: sourceIds,
              candidate_ids: candidateIds,
            }
          )
            .then(function (payload) {
              if (payload.workbench) bench = payload.workbench;
              bulkPicks = {};
              showFlash(payload.confirmation && payload.confirmation.message);
              renderQuestions();
              renderDraft();
              renderMaterials();
            })
            .catch(function (error) {
              showFlash(explainHttpError(error), true);
            });
        },
      }, ["把勾上的 " + ids.length + " 份归到这条问题"]),
      el("button", {
        className: "ghost",
        type: "button",
        onClick: function () {
          bulkPicks = {};
          renderMaterials();
        },
      }, ["全不选"]),
    ]);
  }


  function setAsideCard(item) {
    return el("div", { className: "material set-aside" }, [
      text(item.title || item.url),
      el("div", { className: "action-row", onClick: stopBubble }, [
        el("button", {
          className: "ghost",
          type: "button",
          onClick: function () {
            writeJson(
              "/candidate-sources/" + encodeURIComponent(item.id) + "/restore",
              {}
            )
              .then(function (payload) {
                showFlash(payload.confirmation && payload.confirmation.message);
                return loadBench();
              })
              .catch(function (error) {
                showFlash(explainHttpError(error), true);
              });
          },
        }, ["拿回来"]),
      ]),
    ]);
  }

  function sourceCard(item, untagged) {
    const current = highlightSourceId === item.id || selectedMaterialId === item.id;
    const pending = pendingSnapshotExcerpts && pendingSnapshotExcerpts.sourceId === item.id;
    const children = [
      el("button", {
        className: "material-title",
        type: "button",
        title: text(item.title),
        onClick: function () {
          selectedMaterialId = current ? null : item.id;
          highlightSourceId = selectedMaterialId;
          if (selectedMaterialId !== excerptingSourceId) {
            excerptingSourceId = null;
            excerptClientProvided = false;
          }
          renderMaterials();
        },
      }, [text(item.title)]),
      // 第一层只留名字，点开后再显示出处、链接和操作按钮。
      current && item.question_label
        ? el("p", { className: "why" }, [
            text("对应：" + (item.question_short_label || item.question_label)),
          ])
        : null,
      current && item.original_url
        ? el("p", { className: "why" }, [text(item.original_url)])
        : null,
      current && item.snapshot_note
        ? el("p", { className: "hint" }, [text(item.snapshot_note)])
        : null,
      untagged ? bulkPickBox(item, "source") : null,
      current || pending ? sourceAlwaysActions(item, untagged) : null,
    ];
    if (pending) children.push(pendingExcerptPreview(item));
    else if (current) {
      sourceDetails(item).forEach(function (node) {
        children.push(node);
      });
    }
    return el("div", {
      className: "material"
        + (current || pending ? " current" : "")
        + (item.superseded ? " superseded" : ""),
    }, children);
  }

  function candidateCard(item, untagged) {
    const current = selectedMaterialId === item.id;
    return el("div", {
      className: "material" + (current ? " current" : ""),
      onClick: function () {
        selectedMaterialId = current ? null : item.id;
        renderMaterials();
      },
      title: text(item.title || item.url),
    }, [
      text(item.title || item.url),
      current && item.question_label
        ? el("p", { className: "why" }, [
            text("对应：" + (item.question_short_label || item.question_label)),
          ])
        : null,
      untagged ? bulkPickBox(item, "candidate") : null,
      untagged ? assignQuestionButton(item, true) : null,
      current ? el("p", { className: "hint" }, [text(item.status_label)]) : null,
      current ? el("div", { className: "action-row", onClick: stopBubble }, candidateActions(item)) : null,
    ]);
  }

  function sourceHint(item) {
    if (item.superseded) return item.availability_label + " · 已被更新";
    if (item.supersedes_title) {
      return item.availability_label + " · 更新了「" + item.supersedes_title + "」";
    }
    return item.availability_label;
  }

  function sourceDetails(item) {
    const nodes = [el("p", { className: "hint" }, [text(sourceHint(item))])];
    if (item.limitation) {
      nodes.push(el("p", { className: "hint" }, [text(item.limitation)]));
    }
    (item.excerpts || []).forEach(function (excerpt, index) {
      const prefix = excerpt.locator ? excerpt.locator + " · " : "";
      nodes.push(
        el("p", { className: "excerpt" }, [prefix].concat(
          excerptText("src:" + item.id + ":" + index, excerpt.text)
        ))
      );
    });
    const actions = sourceActionRow(item);
    if (actions) nodes.push(actions);
    return nodes;
  }

  function sourceActionRow(item) {
    if (addFileOpen && supersedeSourceId === item.id) return null;
    if (excerptingSourceId === item.id) return excerptForm(item);
    return el("div", { className: "action-row", onClick: stopBubble }, [
      el("button", {
        className: "ghost",
        type: "button",
        onClick: function () {
          if (!selectedBlockId) {
            showFlash("先点开中间那一节，再把原话挂上去。", true);
            return;
          }
          excerptingSourceId = item.id;
          excerptClientProvided = false;
          addFileOpen = false;
          supersedeSourceId = null;
          renderMaterials();
        },
      }, ["记下这段原文"]),
      el("button", {
        className: "ghost",
        type: "button",
        onClick: function () {
          addFileOpen = true;
          addLinkOpen = false;
          excerptingSourceId = null;
          supersedeSourceId = item.id;
          renderMaterials();
        },
      }, ["用新文件更新这份"]),
    ]);
  }

  function sourceAlwaysActions(item, untagged) {
    const buttons = [];
    if (item.original_url) {
      buttons.push(
        el("button", {
          className: "ghost",
          type: "button",
          onClick: function (event) {
            event.stopPropagation();
            window.open(item.original_url, "_blank", "noopener");
          },
        }, ["打开链接"])
      );
    }
    if (item.can_view_snapshot) {
      buttons.push(
        el("button", {
          className: "ghost",
          type: "button",
          onClick: function (event) {
            event.stopPropagation();
            window.open(
              "/sources/" + encodeURIComponent(item.id) + "/snapshot",
              "_blank",
              "noopener"
            );
          },
        }, ["看快照"])
      );
    }
    if (item.can_scrape_snapshot) {
      buttons.push(
        el("button", {
          className: "ghost",
          type: "button",
          disabled: scrapingSourceId === item.id,
          onClick: function (event) {
            event.stopPropagation();
            scrapeSnapshot(item.id);
          },
        }, [scrapingSourceId === item.id ? "正在扒…" : "从快照扒原话"])
      );
    }
    if (untagged) {
      const assign = assignQuestionButton(item, false);
      if (assign) buttons.push(assign);
    }
    if (!lookbackRound) {
      if (pendingSourceRemovalId === item.id) {
        buttons.push(
          el("button", {
            className: "ghost",
            type: "button",
            onClick: function (event) {
              event.stopPropagation();
              removeSource(item.id);
            },
          }, ["确认去掉"]),
          el("button", {
            className: "quiet",
            type: "button",
            onClick: function (event) {
              event.stopPropagation();
              pendingSourceRemovalId = null;
              renderMaterials();
            },
          }, ["取消"])
        );
      } else {
        buttons.push(
          el("button", {
            className: "quiet",
            type: "button",
            title: "只有还没挂原话的材料能去掉；挂过的删不了，那会把追溯链剪断",
            onClick: function (event) {
              event.stopPropagation();
              pendingSourceRemovalId = item.id;
              renderMaterials();
            },
          }, ["去掉这份"])
        );
      }
    }
    if (!buttons.length) return null;
    return el("div", { className: "action-row", onClick: stopBubble }, buttons);
  }

  function removeSource(sourceId) {
    writeDelete("/sources/" + encodeURIComponent(sourceId))
      .then(function (payload) {
        pendingSourceRemovalId = null;
        if (payload.workbench) bench = payload.workbench;
        if (selectedMaterialId === sourceId) selectedMaterialId = null;
        showFlash(payload.confirmation && payload.confirmation.message);
        renderQuestions();
        renderDraft();
        renderMaterials();
      })
      .catch(function (error) {
        // 挂了原话的会被后端拒绝，把理由原样说给人听，不要吞掉。
        pendingSourceRemovalId = null;
        showFlash(explainHttpError(error), true);
        renderMaterials();
      });
  }

  function assignQuestionButton(item, isCandidate) {
    if (!selectedQuestionId || lookbackRound) return null;
    return el("button", {
      className: "ghost",
      type: "button",
      onClick: function (event) {
        event.stopPropagation();
        const path = isCandidate
          ? "/candidate-sources/" + encodeURIComponent(item.id) + "/question"
          : "/sources/" + encodeURIComponent(item.id) + "/question";
        writeJson(path, { question_id: selectedQuestionId })
          .then(function (payload) {
            if (payload.workbench) bench = payload.workbench;
            delete bulkPicks[item.id];
            showFlash(payload.confirmation && payload.confirmation.message);
            renderQuestions();
            renderDraft();
            renderMaterials();
          })
          .catch(function (error) {
            showFlash(explainHttpError(error), true);
          });
      },
    }, ["归到这条问题"]);
  }

  function pendingExcerptPreview(item) {
    const quotes = (pendingSnapshotExcerpts && pendingSnapshotExcerpts.excerpts) || [];
    return el("div", { className: "preview", onClick: stopBubble }, [
      el("p", { className: "hint" }, ["从快照摘出的原话。收下后才挂到中间这一节。"]),
    ].concat(quotes.map(function (quote) {
      return el("p", { className: "excerpt" }, [text(quote)]);
    })).concat([
      el("div", { className: "action-row" }, [
        el("button", {
          className: "primary",
          type: "button",
          disabled: hangingExcerpt,
          onClick: function () {
            adoptSnapshotExcerpts(item.id);
          },
        }, [hangingExcerpt ? "正在挂上…" : "收下挂到这一节"]),
        el("button", {
          className: "quiet",
          type: "button",
          onClick: function () {
            pendingSnapshotExcerpts = null;
            renderMaterials();
          },
        }, ["丢掉"]),
      ]),
    ]));
  }

  function scrapeSnapshot(sourceId) {
    if (scrapingSourceId) return;
    const questions = (bench && bench.questions) || [];
    if (questions.length && !selectedQuestionId) {
      showFlash("先点开左边要回答的那条，再从快照扒原话。", true);
      return;
    }
    if (!selectedBlockId) {
      showFlash("先点开中间那一节，再从快照扒原话。", true);
      return;
    }
    scrapingSourceId = sourceId;
    selectedMaterialId = sourceId;
    highlightSourceId = sourceId;
    renderMaterials();
    const payload = { deliverable_block_id: selectedBlockId };
    if (selectedQuestionId) payload.question_id = selectedQuestionId;
    writeJson("/sources/" + encodeURIComponent(sourceId) + "/excerpt-draft", payload)
      .then(function (result) {
        scrapingSourceId = null;
        pendingSnapshotExcerpts = {
          sourceId: sourceId,
          excerpts: result.excerpts || [],
        };
        if (result.workbench) bench = result.workbench;
        showFlash(result.confirmation && result.confirmation.message);
        renderMaterials();
      })
      .catch(function (error) {
        scrapingSourceId = null;
        renderMaterials();
        showFlash(explainHttpError(error), true);
      });
  }

  function adoptSnapshotExcerpts(sourceId) {
    if (!pendingSnapshotExcerpts || pendingSnapshotExcerpts.sourceId !== sourceId) return;
    if (!selectedBlockId) {
      showFlash("先点开中间那一节，再把原话挂上去。", true);
      return;
    }
    if (hangingExcerpt) return;
    hangingExcerpt = true;
    renderMaterials();
    writeJson("/sources/" + encodeURIComponent(sourceId) + "/excerpt-draft/adopt", {
      deliverable_block_id: selectedBlockId,
      excerpts: pendingSnapshotExcerpts.excerpts,
    })
      .then(function (result) {
        hangingExcerpt = false;
        pendingSnapshotExcerpts = null;
        selectedMaterialId = sourceId;
        highlightSourceId = sourceId;
        if (result.workbench) bench = result.workbench;
        showFlash(result.confirmation && result.confirmation.message);
        return loadBench();
      })
      .catch(function (error) {
        hangingExcerpt = false;
        renderMaterials();
        showFlash(explainHttpError(error), true);
      });
  }

  function excerptForm(item) {
    const field = el("textarea", {
      className: "excerpt-input",
      rows: "3",
      placeholder: "材料里的原话",
    });
    return el("div", { className: "excerpt-form", onClick: stopBubble }, [
      field,
      el("div", { className: "action-row" }, [
        el("button", {
          className: "progress" + (excerptClientProvided ? " current" : ""),
          type: "button",
          onClick: function () {
            excerptClientProvided = !excerptClientProvided;
            const kept = field.value;
            renderMaterials();
            const next = materialsRoot.querySelector(".excerpt-input");
            if (next) next.value = kept;
          },
        }, ["客户提供"]),
        el("button", {
          className: "primary",
          type: "button",
          disabled: hangingExcerpt,
          onClick: function (event) {
            event.stopPropagation();
            const live = materialsRoot.querySelector(".excerpt-input");
            hangExcerpt(item.id, live && live.value);
          },
        }, [hangingExcerpt ? "正在记下…" : "挂上"]),
        el("button", {
          className: "quiet",
          type: "button",
          onClick: function () {
            excerptingSourceId = null;
            excerptClientProvided = false;
            renderMaterials();
          },
        }, ["取消"]),
      ]),
    ]);
  }

  function hangExcerpt(sourceId, excerpt) {
    const body = text(excerpt).trim();
    if (!body) {
      showFlash("原话不能空着。", true);
      return;
    }
    if (!selectedBlockId) {
      showFlash("先点开中间那一节，再把原话挂上去。", true);
      return;
    }
    if (hangingExcerpt) return;
    hangingExcerpt = true;
    const saveBtn = materialsRoot.querySelector(".excerpt-form .primary");
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "正在记下…";
    }
    const payload = {
      source_id: sourceId,
      excerpt: body,
      text: body,
      epistemic_type: "factual_claim",
    };
    if (excerptClientProvided) payload.provenance_scope = "client_provided";
    writeJson("/deliverable-blocks/" + encodeURIComponent(selectedBlockId) + "/claims", payload)
      .then(function (result) {
        excerptingSourceId = null;
        excerptClientProvided = false;
        hangingExcerpt = false;
        selectedMaterialId = sourceId;
        highlightSourceId = sourceId;
        const message = result.confirmation && result.confirmation.message;
        return loadBench().then(function () {
          showFlash(message || "已记下原话。中间稿还没改，点写这一节才会用上。");
        });
      })
      .catch(function (error) {
        hangingExcerpt = false;
        const btn = materialsRoot.querySelector(".excerpt-form .primary");
        if (btn) {
          btn.disabled = false;
          btn.textContent = "挂上";
        }
        showFlash(explainHttpError(error), true);
      });
  }

  function sourceTitle(sourceId) {
    const sources = ((bench && bench.materials) || {}).sources || [];
    const found = sources.filter(function (item) { return item.id === sourceId; })[0];
    return found ? found.title : "";
  }

  function candidateActions(item) {
    const buttons = [];
    if (item.can_open) {
      buttons.push(
        el("button", {
          className: "ghost",
          type: "button",
          onClick: function () {
            writeJson("/candidate-sources/" + encodeURIComponent(item.id) + "/open", {})
              .then(function () {
                window.open(item.url, "_blank", "noopener");
                return loadBench();
              })
              .catch(function (error) {
                showFlash(explainHttpError(error), true);
              });
          },
        }, ["打开"])
      );
    }
    if (item.can_promote) {
      buttons.push(
        el("button", {
          className: "ghost",
          type: "button",
          onClick: function () {
            writeJson("/candidate-sources/" + encodeURIComponent(item.id) + "/promote", {})
              .then(function () {
                return loadBench();
              })
              .catch(function (error) {
                showFlash(explainHttpError(error), true);
              });
          },
        }, ["用作依据"])
      );
    }
    if (item.can_discard) {
      buttons.push(
        el("button", {
          className: "ghost",
          type: "button",
          onClick: function () {
            writeJson("/candidate-sources/" + encodeURIComponent(item.id) + "/discard", {})
              .then(function (payload) {
                showFlash(payload.confirmation && payload.confirmation.message);
                return loadBench();
              })
              .catch(function (error) {
                showFlash(explainHttpError(error), true);
              });
          },
        }, ["这轮不用"])
      );
    }
    return buttons;
  }

  function feedbackForm() {
    const area = el("textarea", { rows: "6" });
    return el("div", { className: "preview", onClick: stopBubble }, [
      el("p", { className: "hint" }, [
        "经理反馈作为本轮的一份核心材料收进匣子。它是内部指示，不是客户口径，也不是外部证据。",
      ]),
      area,
      el("div", { className: "action-row" }, [
        el("button", {
          className: "primary",
          type: "button",
          onClick: function () {
            const body = text(area.value).trim();
            if (!body) {
              showFlash("经理反馈不能空着。", true);
              return;
            }
            writeJson(
              "/projects/" + encodeURIComponent(projectId) + "/manager-feedback",
              { text: body }
            )
              .then(function (payload) {
                feedbackOpen = false;
                showFlash(payload.confirmation && payload.confirmation.message);
                return loadBench();
              })
              .catch(function (error) {
                showFlash(explainHttpError(error), true);
              });
          },
        }, ["收进材料匣"]),
        el("button", {
          className: "quiet",
          type: "button",
          onClick: function () {
            feedbackOpen = false;
            renderMaterials();
          },
        }, ["取消"]),
      ]),
    ]);
  }

  function untaggedMaterialCount() {
    const materials = (bench && bench.materials) || {};
    const sources = materials.sources || [];
    const candidates = materials.candidates || [];
    var count = 0;
    sources.forEach(function (item) {
      if (!item.research_question_id) count += 1;
    });
    candidates.forEach(function (item) {
      if (!item.research_question_id) count += 1;
    });
    return count;
  }

  function addMaterialControls() {
    const children = [];
    // 搜之前先说一句：手里可能已经躺着现成材料，别急着再去搜。不挡搜索，
    // 只是提醒；搜不搜仍由人决定。
    const untagged = untaggedMaterialCount();
    if (untagged > 0) {
      children.push(
        el("p", { className: "hint search-reminder" }, [
          text(
            "材料匣里还有 " + untagged + " 条没标对应问题，搜之前先看看能不能用上。"
          ),
        ])
      );
    }
    children.push(
      el("button", {
        className: "ghost",
        type: "button",
        disabled: searchingMaterials,
        onClick: searchMaterials,
      }, [searchingMaterials ? "正在搜…" : (selectedQuestionId ? "按这条问题搜" : "按这轮问题搜")]),
      el("button", {
        className: "ghost",
        type: "button",
        onClick: function () {
          feedbackOpen = !feedbackOpen;
          renderMaterials();
        },
      }, ["贴经理反馈"])
    );
    if (feedbackOpen) children.push(feedbackForm());
    if (addFileOpen) {
      // 这一块以前是散着的：文件框、一句说明、一个没有框的文字键、再一排按钮，
      // 字号框线都各写各的（流水账第 4 条）。包成一个面板，一条线对齐。
      const fileRows = [
        el("input", { className: "file-input", type: "file" }),
      ];
      if (supersedeSourceId) {
        fileRows.push(
          el("div", { className: "add-file-line" }, [
            el("p", { className: "hint" }, [
              text("将替代「" + sourceTitle(supersedeSourceId) + "」，旧文件仍保留。"),
            ]),
            el("button", {
              className: "quiet",
              type: "button",
              onClick: function () {
                supersedeSourceId = null;
                renderMaterials();
              },
            }, ["改为新加入"]),
          ])
        );
      }
      children.push(el("div", { className: "add-file" }, fileRows));
      children.push(
        el("div", { className: "action-row" }, [
          el("button", {
            className: "primary",
            type: "button",
            onClick: uploadFile,
          }, ["保存"]),
          el("button", {
            className: "quiet",
            type: "button",
            onClick: function () {
              addFileOpen = false;
              supersedeSourceId = null;
              renderMaterials();
            },
          }, ["取消"]),
        ])
      );
    } else {
      children.push(
        el("button", {
          className: "ghost",
          type: "button",
          onClick: function () {
            addFileOpen = true;
            addLinkOpen = false;
            supersedeSourceId = null;
            excerptingSourceId = null;
            renderMaterials();
          },
        }, ["加入文件"])
      );
    }
    if (addLinkOpen) {
      children.push(
        el("input", { className: "link-input", type: "url", placeholder: "https://" }),
        el("div", { className: "action-row" }, [
          el("button", {
            className: "primary",
            type: "button",
            onClick: addLink,
          }, ["收录"]),
          el("button", {
            className: "quiet",
            type: "button",
            onClick: function () {
              addLinkOpen = false;
              renderMaterials();
            },
          }, ["取消"]),
        ])
      );
    } else {
      children.push(
        el("button", {
          className: "ghost",
          type: "button",
          onClick: function () {
            addLinkOpen = true;
            addFileOpen = false;
            renderMaterials();
          },
        }, ["加入链接"])
      );
    }
    return el("div", { className: "add-line" }, children);
  }

  function searchMaterials() {
    if (searchingMaterials) return;
    const questions = (bench && bench.questions) || [];
    if (questions.length && !selectedQuestionId) {
      showFlash("先点开左边要搜的那条问题，再搜。", true);
      return;
    }
    searchingMaterials = true;
    renderMaterials();
    const payload = {};
    if (selectedQuestionId) payload.question_id = selectedQuestionId;
    writeJson("/projects/" + encodeURIComponent(projectId) + "/material-search", payload)
      .then(function (result) {
        searchingMaterials = false;
        if (result.workbench) bench = result.workbench;
        const added = (result.added && result.added[0]) || null;
        if (added) selectedMaterialId = added.id;
        renderQuestions();
        renderDraft();
        renderMaterials();
        showFlash(result.confirmation && result.confirmation.message);
      })
      .catch(function (error) {
        searchingMaterials = false;
        renderMaterials();
        showFlash(explainHttpError(error), true);
      });
  }

  function uploadFile() {
    const input = materialsRoot.querySelector(".file-input");
    if (!input || !input.files || !input.files[0]) {
      showFlash("请选择本机文件。", true);
      return;
    }
    const data = new FormData();
    data.append("file", input.files[0]);
    if (supersedeSourceId) data.append("supersedes_source_id", supersedeSourceId);
    if (selectedQuestionId) data.append("question_id", selectedQuestionId);
    fetch("/projects/" + encodeURIComponent(projectId) + "/sources", {
      method: "POST",
      body: data,
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          if (!response.ok) throw new Error(payload.error || "没有完成。");
          addFileOpen = false;
          supersedeSourceId = null;
          highlightSourceId = payload.source && payload.source.id;
          selectedMaterialId = highlightSourceId;
          const message = payload.source && payload.source.supersedes_source_id
            ? "已加入材料。旧文件仍保留，相关节可能过时。"
            : "已加入材料。旧文件仍保留。";
          return loadBench().then(function () {
            showFlash(message);
          });
        });
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function addLink() {
    const input = materialsRoot.querySelector(".link-input");
    const url = input && input.value.trim();
    const payload = { url: url };
    if (selectedQuestionId) payload.question_id = selectedQuestionId;
    writeJson("/projects/" + encodeURIComponent(projectId) + "/candidate-sources", payload)
      .then(function () {
        addLinkOpen = false;
        return loadBench().then(function () {
          showFlash("链接已收录。打开后才能当依据。");
        });
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function downloadWord(exporterKey, doneMessage) {
    // 顺手在题目文件夹里留一份，同时保持受控副本不动。
    writeJson(
      "/projects/" + encodeURIComponent(projectId) + "/exports/" + (exporterKey || "word"),
      { save_to_folder: true }
    )
      .then(function (payload) {
        var bytes;
        if (payload.content_encoding === "base64") {
          var binary = atob(payload.content);
          var buffer = new Uint8Array(binary.length);
          for (var i = 0; i < binary.length; i += 1) buffer[i] = binary.charCodeAt(i);
          bytes = buffer;
        } else {
          bytes = payload.content;
        }
        var blob = new Blob([bytes], { type: payload.media_type || "application/octet-stream" });
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = payload.filename || "draft.docx";
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        // 存不存得下都不影响已经下载的那一份，把结果原样说出来。
        showFlash(
          (payload.confirmation && payload.confirmation.message)
            || doneMessage
            || "已下载 Word。核验未改。",
          !!payload.save_error
        );
      })
      .catch(function (error) {
        showFlash(explainHttpError(error), true);
      });
  }

  function initColumnSplit() {
    const root = document.querySelector("main.bench");
    if (!root) return;
    const cols = root.querySelectorAll(".col");
    const splits = root.querySelectorAll(".split");
    if (cols.length !== 3 || splits.length !== 2) return;
    const stored = (function () {
      try {
        const raw = JSON.parse(localStorage.getItem("jingwei-bench-cols") || "null");
        if (
          raw &&
          typeof raw[0] === "number" &&
          typeof raw[1] === "number"
        ) {
          return raw;
        }
      } catch (error) {
        return null;
      }
      return null;
    })();
    let widths = stored || [22, 52];

    function clamp(value, min, max) {
      return Math.min(max, Math.max(min, value));
    }

    function applyWidths() {
      cols[0].style.flex = "0 0 " + widths[0] + "%";
      cols[1].style.flex = "0 0 " + widths[1] + "%";
      cols[2].style.flex = "1 1 auto";
    }

    function saveWidths() {
      try {
        localStorage.setItem("jingwei-bench-cols", JSON.stringify(widths));
      } catch (error) {
        return;
      }
    }

    applyWidths();
    splits.forEach(function (split, index) {
      split.addEventListener("mousedown", function (event) {
        event.preventDefault();
        const startX = event.clientX;
        const start = [widths[0], widths[1]];
        const total = root.getBoundingClientRect().width || 1;
        document.body.classList.add("col-dragging");

        function move(ev) {
          const dx = ((ev.clientX - startX) / total) * 100;
          if (index === 0) {
            widths[0] = clamp(start[0] + dx, 16, 40);
            widths[1] = clamp(start[1] - dx, 28, 70);
          } else {
            widths[1] = clamp(start[1] + dx, 28, 70);
          }
          if (widths[0] + widths[1] > 86) {
            if (index === 0) widths[1] = 86 - widths[0];
            else widths[0] = clamp(86 - widths[1], 16, 40);
          }
          applyWidths();
        }

        function up() {
          document.removeEventListener("mousemove", move);
          document.removeEventListener("mouseup", up);
          document.body.classList.remove("col-dragging");
          saveWidths();
        }

        document.addEventListener("mousemove", move);
        document.addEventListener("mouseup", up);
      });
    });
  }

  goHome.addEventListener("click", showHome);
  exportWord.addEventListener("click", function () {
    downloadWord("word", "已下载整理稿。核验未改。");
  });
  if (exportDetailed) {
    exportDetailed.addEventListener("click", function () {
      downloadWord("word_detailed", "已下载详细版：每节都带原文摘录和机械检查。核验未改。");
    });
  }
  if (homeGuide) {
    homeGuide.addEventListener("click", function () {
      guideOpen = !guideOpen;
      homeGuide.textContent = guideOpen ? "收起" : "模板都是干什么的";
      if (guideOpen) {
        createOpen = false;
        renderCreateForm();
        loadTemplateChoices(renderGuide);
      }
      renderGuide();
    });
  }
  homeNew.addEventListener("click", function () {
    guideOpen = false;
    if (homeGuide) homeGuide.textContent = "模板都是干什么的";
    renderGuide();
    createOpen = true;
    homeTidying = false;
    pendingDeleteId = null;
    renderCreateForm();
    if (homeListing) renderHome(homeListing);
  });
  if (homeTidy) {
    homeTidy.addEventListener("click", function () {
      homeTidying = !homeTidying;
      pendingDeleteId = null;
      if (homeListing) renderHome(homeListing);
    });
  }

  initColumnSplit();
  if (projectId) openProject(projectId);
  else showHome();
})();
