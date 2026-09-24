
const app = {
    init() {
        document.getElementById('ghToken').value = localStorage.getItem('ghToken') || '';
        document.getElementById('ollamaUrl').value = localStorage.getItem('ollamaUrl') || 'http://localhost:11434';
        document.getElementById('cacheSlider').value = localStorage.getItem('cacheSize') || 5;
        document.getElementById('cacheVal').innerText = document.getElementById('cacheSlider').value;
        this.listChatFiles();
        setInterval(this.pollData.bind(this), 3000);
    },
    switchTab(tab) {
        document.getElementById('reposTab').style.display = tab === 'repos' ? 'block' : 'none';
        document.getElementById('chatsTab').style.display = tab === 'chats' ? 'block' : 'none';
        document.querySelectorAll('.nav-btn:not(.outline)').forEach(b => b.classList.remove('active'));
        event.currentTarget.classList.add('active');
    },
    toggleSettings() {
        const m = document.getElementById('settingsModal');
        m.style.display = m.style.display === 'none' ? 'flex' : 'none';
    },
    async saveSettings() {
        localStorage.setItem('ghToken', document.getElementById('ghToken').value);
        localStorage.setItem('ollamaUrl', document.getElementById('ollamaUrl').value);
        localStorage.setItem('cacheSize', document.getElementById('cacheSlider').value);
        await fetch('/api/settings', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({cache_size: document.getElementById('cacheSlider').value}) });
        this.toggleSettings();
    },
    async scanRepos() {
        const token = localStorage.getItem('ghToken');
        if(!token) return alert("Please configure GitHub PAT in settings.");
        document.getElementById('repoStatus').innerText = "⏳ Streaming ZIPs into RAM & Analyzing...";
        await fetch('/api/github/scan', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({token, ollama_url: localStorage.getItem('ollamaUrl'), ollama_model: 'llama3.1'}) });
    },
    async uploadChats() {
        const input = document.getElementById('chatInput');
        if(!input.files.length) return;
        const formData = new FormData();
        for(let f of input.files) formData.append('file', f);
        formData.append('ollama_url', localStorage.getItem('ollamaUrl'));
        formData.append('ollama_model', 'llama3.1');
        document.getElementById('chatStatus').innerText = "⏳ Parsing...";
        const res = await fetch('/api/upload_chats', { method: 'POST', body: formData });
        const data = await res.json();
        document.getElementById('chatStatus').innerText = `✅ Processed ${data.count} chats.`;
        input.value = '';
        this.listChatFiles();
    },
    async listChatFiles() {
        const res = await fetch('/api/chat_files');
        const data = await res.json();
        const list = document.getElementById('chatFilesList');
        if (!data.files || !data.files.length) {
            list.innerHTML = '<div class="muted">No uploaded chat files yet.</div>';
            return;
        }
        list.innerHTML = data.files.map(file => `
            <div class="file-item">
                <span>${file.name}</span>
                <small>${file.uploaded_at}</small>
                <button class="btn outline" onclick="app.deleteChatFile('${file.name}')">Delete</button>
            </div>
        `).join('');
    },
    async deleteChatFile(name) {
        const res = await fetch(`/api/chat_files/${encodeURIComponent(name)}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.status === 'success') {
            document.getElementById('chatStatus').innerText = `🗑️ Removed ${data.removed}.`;
            this.listChatFiles();
        }
    },
    async pollData() {
        const statRes = await fetch('/api/stats');
        const stats = await statRes.json();
        document.getElementById('statLoc').innerText = stats.total_loc;
        document.getElementById('statDead').innerText = stats.abandoned_count;
        const repoRes = await fetch('/api/repos');
        const rData = await repoRes.json();
        this.renderRepos(rData.repos);
    },
    renderRepos(repos) {
        const c = document.getElementById('repoContainer');
        c.innerHTML = repos.map(r => `
            <div class="card">
                <h3>${r.name.split('/')[1] || r.name}</h3>
                <div>
                    <span class="badge tshirt">Effort: ${r.tshirt}</span>
                    <span class="badge mood">Mood: ${r.mood}</span>
                </div>
                <div class="card-desc">${r.description}</div>
                <button class="btn outline full" style="border-color:var(--primary); color:var(--primary);" onclick="app.generateTimeTravel(${r.id})">⏳ Gen Time Travel Prompt</button>
            </div>
        `).join('');
    },
    async generateTimeTravel(repoId) {
        const btn = event.currentTarget; const originalText = btn.innerText; btn.innerText = "⏳ Generating...";
        const res = await fetch(`/api/time_travel/${repoId}`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({token: localStorage.getItem('ghToken')}) });
        const data = await res.json();
        document.getElementById('ttPromptArea').value = data.prompt;
        document.getElementById('ttModal').style.display = 'flex';
        btn.innerText = originalText;
    },
    copyPrompt() {
        const text = document.getElementById('ttPromptArea'); text.select(); document.execCommand('copy');
        alert("Copied!");
    },
    async openGraveyardAnalytics() {
        const res = await fetch('/api/analytics');
        const data = await res.json();
        
        document.getElementById('axFresh').innerText = data.coma_index.Fresh;
        document.getElementById('axDecomp').innerText = data.coma_index.Decomposing;
        document.getElementById('axSkeleton').innerText = data.coma_index.Skeleton;
        document.getElementById('axFossil').innerText = data.coma_index.Fossil;
        
        document.getElementById('axFrustrated').innerText = data.mood_xray.Frustrated;
        document.getElementById('axBored').innerText = data.mood_xray.Bored;
        document.getElementById('axDone').innerText = data.mood_xray.Done;
        
        document.getElementById('axAiScore').innerText = data.ai_match_score + '%';
        
        const maxTs = Math.max(1, data.tshirt_cost.S, data.tshirt_cost.M, data.tshirt_cost.L, data.tshirt_cost.XL);
        document.getElementById('axS').style.height = (data.tshirt_cost.S / maxTs * 100) + 'px';
        document.getElementById('axM').style.height = (data.tshirt_cost.M / maxTs * 100) + 'px';
        document.getElementById('axL').style.height = (data.tshirt_cost.L / maxTs * 100) + 'px';
        document.getElementById('axXL').style.height = (data.tshirt_cost.XL / maxTs * 100) + 'px';
        
        document.getElementById('analyticsModal').style.display = 'flex';
    },
    takeScreenshot() {
        const modal = document.getElementById('analyticsDashboard');
        const btns = modal.querySelector('.no-print');
        btns.style.display = 'none'; // Hide buttons for the screenshot
        
        html2canvas(modal, { backgroundColor: '#0a0a0a', scale: 2 }).then(canvas => {
            const link = document.createElement('a');
            link.download = 'graveyard_analytics.png';
            link.href = canvas.toDataURL('image/png');
            link.click();
            btns.style.display = 'flex'; // Restore buttons
        });
    }
};
document.addEventListener('DOMContentLoaded', () => app.init());
