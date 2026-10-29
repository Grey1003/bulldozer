"use strict";
const research = JSON.parse(document.getElementById("research-data").textContent);

/** 创建仅含文本的页面元素。 */
function element(tag, text, className="") {
    const node=document.createElement(tag);
    node.textContent=text;
    node.className=className;
    return node;
}

/** 绘制规则统计，明确区分通过率和淘汰率。 */
function renderSummary() {
    const target=document.getElementById("summary");
    target.appendChild(element("p",`研究日 ${research.date}；新参数全部入选 ${research.selected_count} 只。`));
    research.groups.forEach(group=>target.appendChild(element("p",
        `${group.label}：通过 ${group.passed}/${group.total}（${(group.passed/group.total*100).toFixed(1)}%）；`+
        `淘汰 ${group.failed}/${group.total}（${(group.failed/group.total*100).toFixed(1)}%）。`+
        `其余条件均通过的 ${group.other_rules_passed} 只中，仅因本条淘汰 ${group.target_only_rejected} 只。`)));
    Plotly.newPlot("scatter",research.scatter.data,research.scatter.layout,{responsive:true,displaylogo:false});
}

/** 每组展示三个通过与三个未通过样本，可展开同源交互图。 */
function renderGroups() {
    const target=document.getElementById("groups");
    research.groups.forEach(group=>{
        const section=element("section","");
        section.appendChild(element("h2",group.label));
        group.samples.forEach(sample=>{
            const card=element("article","");
            card.appendChild(element("h3",sample.title,sample.passed?"pass":"fail"));
            card.appendChild(element("p",`${sample.detail.value}；门槛：${sample.detail.threshold}`));
            card.appendChild(element("p",sample.selected?"本股全部条件通过。":`本股整体未入选：${sample.other_failures}`,"muted"));
            const image=document.createElement("img");image.src=sample.png;image.alt=sample.title;image.loading="lazy";
            card.appendChild(image);
            const details=document.createElement("details");
            details.appendChild(element("summary","展开可缩放K线和日指标门槛图"));
            const chart=element("div","","chart");details.appendChild(chart);
            details.addEventListener("toggle",()=>{
                if (details.open && !chart.dataset.rendered) {
                    Plotly.newPlot(chart,sample.figure.data,sample.figure.layout,{responsive:true,displaylogo:false});
                    chart.dataset.rendered="yes";
                }
            });
            card.appendChild(details);section.appendChild(card);
        });
        target.appendChild(section);
    });
}

renderSummary();
renderGroups();
