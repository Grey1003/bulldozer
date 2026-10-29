"use strict";
const research = JSON.parse(document.getElementById("research-data").textContent);
const strategy = research.parameters.strategy;
const dateInput = document.getElementById("date");
const boundary = document.getElementById("boundary");
dateInput.min = research.calendar[0];
dateInput.max = research.calendar.at(-1);
dateInput.value = new URLSearchParams(location.search).get("date") || research.initial_date;

/** 将比例格式化为百分数。 */
function percent(value) {
    return Number.isFinite(value) ? `${(value * 100).toFixed(4)}%` : "历史不足";
}

/** 追加文本单元格，避免将股票名称或诊断内容作为HTML执行。 */
function appendRow(table, values, passed = null) {
    const row = document.createElement("tr");
    values.forEach(value => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
    });
    if (passed !== null) row.lastChild.className = passed ? "pass" : "fail";
    table.appendChild(row);
}

/** 日期仅映射到此前交易日，越过缓存边界时拒绝显示结果。 */
function selectedDate() {
    const value = dateInput.value;
    if (value < dateInput.min || value > dateInput.max) return null;
    return research.calendar.filter(date => date <= value).at(-1);
}

/** 用截至选定日期的价格前缀绘图，前复权归一化也只取该前缀末日因子。 */
function drawChart(date) {
    const prefix = research.prices.filter(row => row.date <= date);
    if (!prefix.length) {
        Plotly.purge("chart");
        document.getElementById("chart").textContent = "所选日期尚无该股票的本地行情。";
        return [];
    }
    const rows = prefix.slice(-research.parameters.report.chart_bars);
    const anchor = prefix.at(-1).factor;
    const dates = rows.map(row => row.date);
    const prices = key => rows.map(row => row[key] === null ? null : row[key] / anchor);
    const colors = rows.map(row => row.close >= row.open ? "#dc2626" : "#059669");
    const traces = [{type:"candlestick", name:"日K", x:dates, open:prices("open"),
        high:prices("high"), low:prices("low"), close:prices("close"),
        increasing:{line:{color:"#dc2626"}}, decreasing:{line:{color:"#059669"}}}];
    [5,10,20,60].forEach((period,index) => traces.push({type:"scatter", mode:"lines",
        name:`MA${period}`, x:dates, y:prices(`ma${period}`),
        line:{color:["#d99400","#2563eb","#b438b4","#64748b"][index], width:1.4}}));
    const levels = boundary.value === "close_distance" ? [[1+strategy.max_close_ma5_distance,"收盘上限"]]
        : boundary.value === "support" ? [[1-strategy.max_below_ma5,"收盘支撑底线"]]
        : boundary.value === "touch_ma5" ? [[1+strategy.max_low_ma5_distance,"低点贴线上沿"],
            [1-strategy.max_low_ma5_distance,"低点贴线下沿"]] : [];
    levels.forEach(([multiple,label]) => traces.push({type:"scatter", mode:"lines",
        x:dates, y:prices("ma5").map(value => value === null ? null : value*multiple),
        name:label, line:{dash:"dot",width:1.3,color:"#a855f7"}}));
    traces.push({type:"bar", x:dates, y:rows.map(row=>row.volume), name:"原始成交量",
        marker:{color:colors}, xaxis:"x2", yaxis:"y2"});
    const closeDistances = rows.map(row => row.ma5 ? row.close/row.ma5-1 : null);
    const lowDistances = rows.map(row => row.ma5 ? row.low/row.ma5-1 : null);
    const auxiliary = boundary.value === "no_touch_ma20" ? rows.map(row=>row.ma20 ? row.low/row.ma20-1 : null)
        : boundary.value === "no_acceleration" ? rows.map(row=>row.daily_return)
        : boundary.value === "touch_ma5" ? lowDistances : closeDistances;
    traces.push({type:"scatter", mode:"lines+markers", x:dates, y:auxiliary,
        name:boundary.value === "no_touch_ma20" ? "最低价距MA20"
            : boundary.value === "no_acceleration" ? "每日真实收益率" : "每日距MA5",
        xaxis:"x3", yaxis:"y3", line:{color:"#334155"}, marker:{size:4}});
    const cutoffs = boundary.value === "no_touch_ma20" ? [0]
        : boundary.value === "close_distance" ? [strategy.max_close_ma5_distance]
        : boundary.value === "support" ? [-strategy.max_below_ma5]
        : boundary.value === "touch_ma5" ? [-strategy.max_low_ma5_distance,strategy.max_low_ma5_distance] : [];
    const shapes = cutoffs.map(level=>({type:"line",xref:"x3 domain",x0:0,x1:1,
        yref:"y3",y0:level,y1:level,line:{color:"#b91c1c",dash:"dash"}}));
    const windowDates = research.calendar.filter(value=>value<=date).slice(-strategy.trend_days);
    shapes.push({type:"rect",xref:"x",yref:"paper",x0:windowDates[0],x1:date,
        y0:0,y1:1,fillcolor:"#fde68a",opacity:.2,line:{width:0},layer:"below"});
    if (boundary.value === "no_acceleration") {
        const splitDate = windowDates.at(-strategy.acceleration_days);
        shapes.push({type:"line",xref:"x",yref:"paper",x0:splitDate,x1:splitDate,
            y0:0,y1:1,line:{color:"#ea580c",dash:"dot"}});
    }
    if (boundary.value === "no_touch_ma20") {
        const touchDates = research.calendar.filter(value=>value<=date).slice(-strategy.ma20_touch_days);
        const touched = rows.filter(row=>touchDates.includes(row.date) && row.low<=row.ma20);
        shapes.push({type:"rect",xref:"x",yref:"paper",x0:touchDates[0],x1:date,
            y0:0,y1:1,fillcolor:"#fca5a5",opacity:.2,line:{width:0},layer:"below"});
        traces.push({type:"scatter",mode:"markers",name:"近窗最低价触及MA20",
            x:touched.map(row=>row.date),y:touched.map(row=>row.low/anchor),
            marker:{color:"#b91c1c",size:10,symbol:"x"}});
    }
    const layout = {title:`${research.name} ${research.code} · 截至 ${date}`,
        height:760,margin:{l:65,r:25,t:90,b:50},paper_bgcolor:"white",plot_bgcolor:"white",
        legend:{orientation:"h",y:1.05},hovermode:"x unified",shapes,
        xaxis:{type:"category",domain:[0,1],anchor:"y",showticklabels:false,rangeslider:{visible:false}},
        yaxis:{domain:[.43,1],title:"研究日时点前复权 / 元"},
        xaxis2:{type:"category",matches:"x",anchor:"y2",showticklabels:false},
        yaxis2:{domain:[.23,.38],title:"原始成交量"},
        xaxis3:{type:"category",matches:"x",anchor:"y3",nticks:10},
        yaxis3:{domain:[0,.18],title:"日指标",tickformat:".0%"}};
    Plotly.react("chart",traces,layout,{responsive:true,displaylogo:false});
    return rows.filter(row=>windowDates.includes(row.date));
}

/** 同步显示Python判定、逐日偏离及截至所选日期的图形。 */
function render() {
    const date = selectedDate();
    if (!date) {
        document.getElementById("status").textContent="输入日期超出缓存范围";
        document.getElementById("reason").textContent="请选择允许范围内的日期。";
        document.getElementById("effective-date").textContent="";
        document.getElementById("rules").replaceChildren();
        document.getElementById("daily").replaceChildren();
        document.getElementById("annotations").textContent="";
        Plotly.purge("chart");
        return;
    }
    const diagnosis = research.diagnoses[date];
    const status = document.getElementById("status");
    status.textContent = diagnosis.selected ? "符合全部推土机条件" : "不符合全部推土机条件";
    status.className = diagnosis.selected ? "pass" : "fail";
    document.getElementById("reason").textContent=diagnosis.reason || `全部资格和${diagnosis.details.length}项形态规则通过。`;
    document.getElementById("effective-date").textContent=`输入 ${dateInput.value} → 实际交易日 ${date}；名称为缓存下载时名称。黄色区域为最后${strategy.trend_days}个交易日。`;
    const rules=document.getElementById("rules");rules.replaceChildren();
    diagnosis.details.forEach(row=>appendRow(rules,[row.label,row.value,row.threshold,row.passed?"通过":"未通过"],row.passed));
    document.getElementById("annotations").textContent=diagnosis.annotations.map(row=>`${row.label}：${row.value}。${row.threshold}`).join("；") || "资格不足，未评估二波反弹。";
    const daily=document.getElementById("daily");daily.replaceChildren();
    drawChart(date).forEach(row=>{
        const closeDistance=row.close/row.ma5-1;
        const lowDistance=row.low/row.ma5-1;
        appendRow(daily,[row.date,percent(closeDistance),percent(lowDistance),
            Math.abs(lowDistance)<=strategy.max_low_ma5_distance?"贴线":"不贴线",
            closeDistance<=strategy.max_close_ma5_distance?"通过":"未通过",
            closeDistance>=-strategy.max_below_ma5?"通过":"未通过"]);
    });
    const index=research.calendar.indexOf(date);
    document.getElementById("previous").disabled=index<=0;
    document.getElementById("next").disabled=index>=research.calendar.length-1;
}

/** 按交易日历切换，避免把周末当成行情。 */
function moveDate(step) {
    const index=research.calendar.indexOf(selectedDate());
    dateInput.value=research.calendar[Math.min(Math.max(index+step,0),research.calendar.length-1)];
    render();
}

dateInput.addEventListener("change",render);
boundary.addEventListener("change",render);
document.getElementById("previous").addEventListener("click",()=>moveDate(-1));
document.getElementById("next").addEventListener("click",()=>moveDate(1));
render();
