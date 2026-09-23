let monteCarloChart;
const mcText = (id, value) => { document.getElementById(id).textContent = value; };
const mcMoney = n => '$' + n.toFixed(3);
const mcPercent = n => (100*n).toFixed(1) + '%';
function mcTable(id, headers, rows) {
    const table = document.createElement('table');
    table.style.cssText = 'width:100%;border-collapse:collapse;margin:16px 0';
    [headers, ...rows].forEach((row, i) => {
        const tr = document.createElement('tr');
        row.forEach(value => {
            const cell = document.createElement(i ? 'td' : 'th');
            cell.textContent = value;
            cell.style.cssText = 'padding:8px;text-align:left;border-bottom:1px solid #ddd';
            tr.appendChild(cell);
        });
        table.appendChild(tr);
    });
    document.getElementById(id).replaceChildren(table);
}
async function runMonteCarloSimulation() {
    const button = document.getElementById('mc-run');
    if (button.disabled) return;
    const raw = document.getElementById('mc-current-price').value.trim();
    const price = raw === '' ? null : Number(raw);
    mcText('mc-error', '');
    document.getElementById('mc-results').style.display = 'none';
    if (price !== null && (!Number.isFinite(price) || price <= 0)) {
        mcText('mc-error', 'Enter a positive scenario price or leave it blank.'); return;
    }
    button.disabled = true;
    document.getElementById('mc-loading').style.display = 'block';
    try {
        const response = await fetch('/api/monte_carlo', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({current_price:price, days:Number(document.getElementById('mc-days').value),
                n_simulations:Number(document.getElementById('mc-simulations').value),
                model:document.getElementById('mc-model').value, seed:42})
        });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || 'Simulation unavailable');
        const r = data.results, s = r.statistics, q = r.data_quality;
        mcText('mc-expected-price', mcMoney(s.median_final_price));
        mcText('mc-profit-prob', mcPercent(s.probability_profit));
        mcText('mc-var', mcPercent(s.var_95));
        mcText('mc-risk-level', data.summary.risk_assessment);
        mcText('mc-recommendation', data.summary.recommendation);
        mcText('mc-data-info', `${q.starting_price_source}: ${mcMoney(r.simulation_params.current_price)}; quote: ${q.quote_timestamp || 'scenario'}. History through ${q.history_end}; ${q.calibration_returns} daily returns; model: ${r.simulation_params.model}.`);
        mcText('mc-tail-risk', `90% model range: ${mcMoney(s.percentiles['5th'])}–${mcMoney(s.percentiles['95th'])}. Average loss in worst 5%: ${mcPercent(s.expected_shortfall_95)}. Chance of price falling over 10%: ${mcPercent(s.probability_loss_10pct)}. 95th percentile maximum drawdown: ${mcPercent(r.risk_metrics.drawdown_95)}.`);
        mcTable('mc-horizons', ['Trading days','Median','90% range','Chance of rise','95% loss VaR'], r.horizons.map(h => [h.days,mcMoney(h.median_final_price),mcMoney(h.percentiles['5th'])+'–'+mcMoney(h.percentiles['95th']),mcPercent(h.probability_profit),mcPercent(h.var_95)]));
        mcTable('mc-backtest', ['Days','Model','Test windows','90% coverage','Interval score','Median error','Unchanged-price error'], r.backtest.results.map(b => [b.days,b.model,b.origins+(b.limited_sample?' (limited)':''),mcPercent(b.coverage_90),b.interval_score.toFixed(3),mcPercent(b.median_absolute_error),mcPercent(b.unchanged_price_error)]));
        mcText('mc-limitations', r.limitations.join(' ')+' '+r.backtest.note);
        document.getElementById('mc-results').style.display = 'block';
        if (typeof Chart !== 'undefined') {
            if (monteCarloChart) monteCarloChart.destroy();
            monteCarloChart = new Chart(document.getElementById('mc-fan'), {
                type:'line', data:{labels:r.fan_chart.days, datasets:[
                    {label:'5th percentile',data:r.fan_chart.p5,borderColor:'#94a3b8',pointRadius:0},
                    {label:'95th percentile',data:r.fan_chart.p95,borderColor:'#94a3b8',backgroundColor:'#dbeafe80',fill:'-1',pointRadius:0},
                    {label:'Median',data:r.fan_chart.p50,borderColor:'#2563eb',pointRadius:0}]},
                options:{responsive:true,maintainAspectRatio:false,animation:false,scales:{x:{title:{display:true,text:'Trading days'}},y:{title:{display:true,text:'Copper price ($/lb)'}}}}
            });
        }
    } catch (error) { mcText('mc-error', error.message || 'Simulation unavailable; please retry.'); }
    finally { button.disabled=false; document.getElementById('mc-loading').style.display='none'; }
}
window.runMonteCarloSimulation = runMonteCarloSimulation;
