const app = {
    selectedRepos: new Set(),
    selectedChats: new Set(),
    tags: [],
    confidence: 70,
    assignTarget: 'repos',
    repoMap: {},
    linkRepoId: null,
    init() {
        const urlField = document.getElementById('ollamaUrl');
        const savedUrl = localStorage.getItem('ollamaUrl');
        urlField.value = savedUrl || '';
        document.getElementById('ghToken').value = localStorage.getItem('ghToken') || '';
        document.getElementById('cacheSlider').value = localStorage.getItem('cacheSize') || 5;
        document.getElementById('cacheVal').innerText = document.getElementById('cacheSlider').value;
        urlField.addEventListener('change', () => this.refreshOllamaModels());
        urlField.addEventListener('blur', () => this.refreshOllamaModels());
        this.loadTags();
        this.resolveOllamaUrl();
        this.listChatFiles();
        this.loadSettings();
        setInterval(this.pollData.bind(this), 3000);
    },
    async loadTags() {
        const list = document.getElementById('tagListEditor');
        if (!list) return;
        try {
            const res = await fetch('/api/tags');
            const data = await res.json();
            const tags = Array.isArray(data.tags) ? data.tags : [];
            list.innerHTML = tags.length ? tags.map(tag => {
                const color = tag.color || '#60a5fa';
                const name = tag.name || tag.id || 'Untitled';
                return `
                    <div class="tag-item">
                        <span class="tag-pill" style="--tag-color:${color}; background:${color}18; border-color:${color};">${name}</span>
                        <div class="tag-actions">
                            <button class="btn outline small" type="button" onclick="app.renameTag('${tag.id}')">Rename</button>
                            <button class="btn outline small danger" type="button" onclick="app.deleteTag('${tag.id}')">Delete</button>
                        </div>
                    </div>
                `;
            }).join('') : '<div class="muted">No tags defined yet.</div>';
        } catch (error) {
            list.innerHTML = '<div class="muted">Unable to load tag list.</div>';
        }
    },
    async addTag() {
        const nameInput = document.getElementById('tagNameInput');
        const colorInput = document.getElementById('tagColorInput');
        const name = nameInput?.value.trim();
        if (!name) {
            alert('Tag name is required.');
            return;
        }
        const payload = { name, color: colorInput?.value || '#60a5fa' };
        const res = await fetch('/api/tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) {
            alert(data.error || 'Unable to add tag.');
            return;
        }
        nameInput.value = '';
        colorInput.value = '#60a5fa';
        await this.loadTags();
    },
    async deleteTag(tagId) {
        if (!tagId || !confirm('Delete this tag from the shared taxonomy?')) return;
        const res = await fetch(`/api/tags/${encodeURIComponent(tagId)}`, { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) {
            alert(data.error || 'Unable to delete tag.');
            return;
        }
        await this.loadTags();
    },
    async renameTag(tagId) {
        if (!tagId) return;
        const res = await fetch('/api/tags');
        const data = await res.json();
        const tag = (data.tags || []).find(item => String(item.id) === String(tagId));
        if (!tag) return;
        const updatedName = window.prompt('Rename tag', tag.name || tag.id || '');
        if (updatedName === null) return;
        const name = updatedName.trim();
        if (!name) {
            alert('Tag name is required.');
            return;
        }
        const putRes = await fetch(`/api/tags/${encodeURIComponent(tagId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, color: tag.color || '#60a5fa' })
        });
        const putData = await putRes.json();
        if (!putRes.ok) {
            alert(putData.error || 'Unable to rename tag.');
            return;
        }
        await this.loadTags();
    },
    async loadSettings() {
        try {
            const res = await fetch('/api/settings');
            const data = await res.json();
            const settings = data.settings || {};
            this.confidence = Number(settings.confidence ?? 70);
            this.tags = data.tags || [];
            const backend = document.getElementById('layaBackend');
            const model = document.getElementById('layaModel');
            const confidence = document.getElementById('layaConfidence');
            const zipToggle = document.getElementById('zipProcessingToggle');
            if (backend) backend.value = settings.laya_backend || 'auto';
            if (model) model.value = settings.laya_model || 'facebook/bart-large-mnli';
            if (confidence) {
                confidence.value = this.confidence;
                document.getElementById('layaConfidenceVal').innerText = this.confidence;
            }
            if (zipToggle) zipToggle.checked = settings.zip_processing_enabled !== false;
        } catch (error) {
            this.tags = [];
        }
    },
    async resolveOllamaUrl() {
        const urlField = document.getElementById('ollamaUrl');
        try {
            const res = await fetch('/api/ollama/resolve-url');
            const data = await res.json();
            const resolved = data.url || 'http://localhost:11434';
            urlField.value = resolved;
            localStorage.setItem('ollamaUrl', resolved);
            await this.refreshOllamaModels();
        } catch (error) {
            if (!urlField.value) {
                urlField.value = 'http://localhost:11434';
                localStorage.setItem('ollamaUrl', urlField.value);
            }
            await this.refreshOllamaModels();
            await this.loadSettings();
        }
    },
    async refreshOllamaModels() {
        const url = document.getElementById('ollamaUrl')?.value || localStorage.getItem('ollamaUrl') || 'http://localhost:11434';
        const select = document.getElementById('ollamaModel');
        if (!select) return;

        const savedModel = localStorage.getItem('ollamaModel') || 'llama3.1';
        select.innerHTML = '<option value="llama3.1">Loading models…</option>';
        select.disabled = true;

        try {
            const res = await fetch(`/api/ollama/models?url=${encodeURIComponent(url)}`);
            const data = await res.json();
            const models = Array.isArray(data.models) && data.models.length ? data.models : ['llama3.1'];
            select.innerHTML = models.map(model => `<option value="${model}">${model}</option>`).join('');
            const chosen = models.includes(savedModel) ? savedModel : models[0];
            select.value = chosen;
            localStorage.setItem('ollamaModel', chosen);
        } catch (error) {
            select.innerHTML = '<option value="llama3.1">llama3.1</option>';
            localStorage.setItem('ollamaModel', 'llama3.1');
        } finally {
            select.disabled = false;
        }
    },
    switchTab(tab) {
        document.getElementById('reposTab').style.display = tab === 'repos' ? 'block' : 'none';
        document.getElementById('chatsTab').style.display = tab === 'chats' ? 'block' : 'none';
        document.querySelectorAll('.nav-btn:not(.outline)').forEach(b => b.classList.remove('active'));
        event.currentTarget.classList.add('active');
        if (tab === 'chats') this.pollChats();
    },
    async toggleSettings() {
        const m = document.getElementById('settingsModal');
        const isHidden = m.style.display === 'none' || !m.style.display;
        m.style.display = isHidden ? 'flex' : 'none';
        if (isHidden) {
            await this.refreshOllamaModels();
            await this.loadTags();
        }
    },
    async saveSettings() {
        const url = document.getElementById('ollamaUrl').value.trim() || 'http://localhost:11434';
        const model = document.getElementById('ollamaModel')?.value || localStorage.getItem('ollamaModel') || 'llama3.1';
        localStorage.setItem('ghToken', document.getElementById('ghToken').value);
        localStorage.setItem('ollamaUrl', url);
        localStorage.setItem('ollamaModel', model);
        localStorage.setItem('cacheSize', document.getElementById('cacheSlider').value);
        const payload = {
            cache_size: document.getElementById('cacheSlider').value,
            ollama_url: url,
            ollama_model: model,
            zip_processing_enabled: document.getElementById('zipProcessingToggle')?.checked !== false,
        };
        await fetch('/api/settings', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
        this.toggleSettings();
    },
    async openLayaSettings() {
        await this.loadSettings();
        document.getElementById('layaModal').style.display = 'flex';
    },
    async saveLayaSettings() {
        const body = {
            laya_backend: document.getElementById('layaBackend').value,
            laya_model: document.getElementById('layaModel').value,
            confidence: Number(document.getElementById('layaConfidence').value),
        };
        await fetch('/api/settings', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
        this.confidence = body.confidence;
        document.getElementById('layaModal').style.display = 'none';
        this.pollData();
    },
    async openTagEditor() {
        await this.refreshTagEditor();
        document.getElementById('tagModal').style.display = 'flex';
    },
    async refreshTagEditor() {
        const res = await fetch('/api/tags');
        const data = await res.json();
        this.tags = data.tags || [];
        const list = document.getElementById('tagEditorList');
        list.innerHTML = this.tags.map(tag => `
            <div class="tag-editor-row" data-tag-id="${tag.id}">
                <input type="color" value="${tag.color || '#60a5fa'}" data-role="color">
                <input type="text" value="${this.escape(tag.name)}" data-role="name">
                <button class="btn outline" data-action="save">Save</button>
                <button class="btn outline" data-action="delete">Delete</button>
            </div>
        `).join('');
        list.querySelectorAll('.tag-editor-row').forEach(row => {
            const id = row.dataset.tagId;
            row.querySelector('[data-action="save"]').onclick = () => this.updateTag(id, row);
            row.querySelector('[data-action="delete"]').onclick = () => this.deleteTag(id);
        });
    },
    async createTag() {
        const name = document.getElementById('newTagName').value.trim();
        const color = document.getElementById('newTagColor').value;
        if (!name) return;
        await fetch('/api/tags', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({name, color}) });
        document.getElementById('newTagName').value = '';
        await this.refreshTagEditor();
    },
    async updateTag(id, row) {
        const name = row.querySelector('[data-role="name"]').value.trim();
        const color = row.querySelector('[data-role="color"]').value;
        await fetch(`/api/tags/${encodeURIComponent(id)}`, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({name, color}) });
        await this.refreshTagEditor();
    },
    async deleteTag(id) {
        await fetch(`/api/tags/${encodeURIComponent(id)}`, { method: 'DELETE' });
        await this.refreshTagEditor();
    },
    async scanRepos() {
        const token = localStorage.getItem('ghToken');
        if(!token) return alert("Please configure GitHub PAT in settings.");
        const url = localStorage.getItem('ollamaUrl') || 'http://localhost:11434';
        const model = localStorage.getItem('ollamaModel') || 'llama3.1';
        document.getElementById('repoStatus').innerText = "Listing repositories...";
        const res = await fetch('/api/github/scan', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({token, ollama_url: url, ollama_model: model}) });
        const data = await res.json();
        if (data.repos) {
            this.renderRepoProgress(data);
            this.renderRepos(data.repos);
        }
        document.getElementById('repoStatus').innerText = data.error || '';
    },
    async uploadChats() {
        const input = document.getElementById('chatInput');
        if(!input.files.length) return;
        const formData = new FormData();
        for(let f of input.files) formData.append('file', f);
        formData.append('ollama_url', localStorage.getItem('ollamaUrl') || 'http://localhost:11434');
        formData.append('ollama_model', localStorage.getItem('ollamaModel') || 'llama3.1');
        document.getElementById('chatStatus').innerText = "⏳ Parsing...";
        const res = await fetch('/api/upload_chats', { method: 'POST', body: formData });
        const data = await res.json();
        document.getElementById('chatStatus').innerText = `✅ Processed ${data.count} chats.`;
        input.value = '';
        this.listChatFiles();
        this.pollChats();
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
                <span>${this.escape(file.name)}</span>
                <small>${this.escape(file.uploaded_at)}</small>
                <button class="btn outline" onclick="app.deleteChatFile('${this.escape(file.name)}')">Delete</button>
            </div>
        `).join('');
        document.getElementById('assignModal').style.display = 'flex';
    },
    async submitAssign(action) {
        const tag_ids = [...document.querySelectorAll('#assignTagList input:checked')].map(input => input.value);
        const body = {action, tag_ids, repo_ids: [], chat_ids: []};
        if (this.assignTarget === 'repos') body.repo_ids = [...this.selectedRepos];
        else body.chat_ids = [...this.selectedChats];
        await fetch('/api/tags/assign', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
        document.getElementById('assignModal').style.display = 'none';
        this.pollData();
        this.pollChats();
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
        this.renderRepoProgress(rData);
        this.renderLinkProgress(rData);
        this.renderRepos(rData.repos || []);
        if (document.getElementById('chatsTab').style.display !== 'none') {
            await this.pollChats();
        }
    },
    renderRepoProgress(data) {
        const total = data.total_count || 0;
        const analyzed = data.analyzed_count || 0;
        const block = document.getElementById('repoProgress');
        if (!total) {
            block.hidden = true;
            return;
        }
        block.hidden = false;
        const pct = Math.round((analyzed / total) * 100);
        document.getElementById('repoProgressBar').style.width = pct + '%';
        document.getElementById('repoProgressLabel').textContent = `${pct}% · ${analyzed}/${total} ready · ${data.cached_count || 0} cached`;
        const lines = (data.scan_log || []).map(entry => entry.message).join('\n');
        document.getElementById('scanLog').textContent = lines;
    },
    renderLinkProgress(data) {
        const block = document.getElementById('linkProgress');
        const total = data.link_total || 0;
        if (!data.link_in_progress || !total) {
            block.hidden = true;
            return;
        }
        block.hidden = false;
        const done = data.link_done || 0;
        const pct = Math.round((done / total) * 100);
        document.getElementById('linkProgressBar').style.width = pct + '%';
        document.getElementById('linkProgressLabel').textContent = `${pct}% · ${done}/${total} repos`;
    },
    async linkConversations() {
        const status = document.getElementById('repoStatus');
        status.innerText = 'Linking conversations...';
        const res = await fetch('/api/link_chats', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                ollama_url: localStorage.getItem('ollamaUrl') || 'http://localhost:11434',
                ollama_model: localStorage.getItem('ollamaModel') || 'llama3.1',
            }),
        });
        const data = await res.json();
        if (data.error) {
            status.innerText = data.error;
            return;
        }
        status.innerText = 'Linking conversations...';
        this.renderLinkProgress(data);
        this.renderRepos(data.repos || []);
    },
    renderRepos(repos) {
        const c = document.getElementById('repoContainer');
        this.repoMap = {};
        const seen = new Set();
            repos.forEach(repo => {
            const id = String(repo.id);
            this.repoMap[id] = repo;
            seen.add(id);
            let card = c.querySelector(`[data-repo-id="${id}"]`);
            if (!card) {
                card = document.createElement('div');
                card.className = 'card';
                card.dataset.repoId = id;
                card.innerHTML = `
                    <label class="card-check"><input type="checkbox"></label>
                    <h3 data-field="title"></h3>
                    <div class="chip-row" data-field="status"></div>
                    <div class="chip-row" data-field="badges"></div>
                    <div class="chip-row" data-field="tags"></div>
                    <div class="card-desc" data-field="description"></div>
                    <button type="button" class="btn outline full links-btn"></button>
                    <button class="btn outline full travel-btn" style="border-color:var(--primary); color:var(--primary);">⏳ Gen Time Travel Prompt</button>
                `;
                card.querySelector('input').addEventListener('change', (event) => {
                    if (event.target.checked) this.selectedRepos.add(id);
                    else this.selectedRepos.delete(id);
                    this.updateBulk('repos');
                });
                card.querySelector('.links-btn').addEventListener('click', () => this.openLinks(id));
                card.querySelector('.travel-btn').addEventListener('click', (event) => this.generateTimeTravel(id, event));
                c.appendChild(card);
            }
            const title = (repo.name || '').split('/').pop() || repo.name || '';
            card.querySelector('[data-field="title"]').textContent = title;
            const status = repo.status || 'pending';
            card.querySelector('[data-field="status"]').innerHTML = `<span class="badge status-${status}">${status}${repo.cache_hit ? ' · cache' : ''}</span>`;
            card.querySelector('[data-field="badges"]').innerHTML = `
                <span class="badge tshirt">Effort: ${this.escape(repo.tshirt || '…')}</span>
                <span class="badge mood">Mood: ${this.escape(repo.mood || '…')}</span>
            `;
            card.querySelector('[data-field="tags"]').innerHTML = this.visibleTags(repo).map(name => `<span class="badge">${this.escape(name)}</span>`).join('');
            card.querySelector('[data-field="description"]').textContent = repo.description || '';
            const links = repo.linked_chats || [];
            const linkButton = card.querySelector('.links-btn');
            linkButton.textContent = links.length === 1 ? '1 conversation' : `${links.length} conversations`;
            const button = card.querySelector('.travel-btn');
            button.disabled = status !== 'ready';
            card.querySelector('input').checked = this.selectedRepos.has(id);
        });
        c.querySelectorAll('[data-repo-id]').forEach(card => {
            if (!seen.has(card.dataset.repoId)) card.remove();
        });
        if (this.linkRepoId && document.getElementById('linkModal').style.display === 'flex') {
            this.renderLinkList(this.linkRepoId);
        }
    },
    openLinks(repoId) {
        this.linkRepoId = String(repoId);
        this.renderLinkList(this.linkRepoId);
        document.getElementById('linkModal').style.display = 'flex';
    },
    renderLinkList(repoId) {
        const repo = this.repoMap[String(repoId)];
        const title = document.getElementById('linkModalTitle');
        const list = document.getElementById('linkList');
        if (!repo) {
            title.textContent = 'Linked conversations';
            list.innerHTML = '<div class="muted">No conversations linked.</div>';
            return;
        }
        const name = (repo.name || '').split('/').pop() || repo.name || 'Repository';
        title.textContent = `Linked conversations · ${name}`;
        const links = repo.linked_chats || [];
        if (!links.length) {
            list.innerHTML = '<div class="muted">No conversations linked.</div>';
            return;
        }
        list.innerHTML = links.map(link => `
            <div class="link-row" data-fingerprint="${this.escape(link.fingerprint || '')}">
                <div>
                    <strong>${this.escape(link.title || 'Conversation')}</strong>
                    <div class="muted">${this.escape(link.reason || '')}</div>
                    <div class="muted">Score ${this.escape(String(link.score ?? ''))}${link.pinned ? ' · pinned' : ''}</div>
                </div>
                <div class="link-actions">
                    <button type="button" class="btn outline" data-action="open" ${link.available ? '' : 'disabled'}>Open</button>
                    <button type="button" class="btn outline" data-action="pin">${link.pinned ? 'Unpin' : 'Pin'}</button>
                    <button type="button" class="btn outline" data-action="unlink">Unlink</button>
                </div>
            </div>
        `).join('');
        list.querySelectorAll('.link-row').forEach(row => {
            const fingerprint = row.dataset.fingerprint;
            const link = links.find(item => item.fingerprint === fingerprint);
            row.querySelector('[data-action="open"]').addEventListener('click', () => {
                if (link && link.chat_id) this.openChat(link.chat_id);
            });
            row.querySelector('[data-action="pin"]').addEventListener('click', () => {
                this.updateLink(fingerprint, link && link.pinned ? 'unpin' : 'pin');
            });
            row.querySelector('[data-action="unlink"]').addEventListener('click', () => this.updateLink(fingerprint, 'unlink'));
        });
    },
    async updateLink(fingerprint, action) {
        if (!this.linkRepoId || !fingerprint) return;
        await fetch(`/api/repos/${encodeURIComponent(this.linkRepoId)}/links/${encodeURIComponent(fingerprint)}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action}),
        });
        await this.pollData();
    },
    visibleTags(entity) {
        const threshold = (Number(this.confidence) || 0) / 100;
        const names = new Set();
        (entity.manual_tags || []).forEach(id => {
            const tag = this.tags.find(item => item.id === id || item.name === id);
            names.add(tag ? tag.name : id);
        });
        Object.entries(entity.tags || {}).forEach(([name, score]) => {
            if (Number(score) >= threshold) names.add(name);
        });
        (entity.visible_tags || entity.tag_names || []).forEach(name => names.add(name));
        return [...names];
    },
    updateBulk(kind) {
        const selected = kind === 'repos' ? this.selectedRepos : this.selectedChats;
        const bar = document.getElementById(kind === 'repos' ? 'repoBulk' : 'chatBulk');
        const label = document.getElementById(kind === 'repos' ? 'repoBulkCount' : 'chatBulkCount');
        bar.hidden = selected.size === 0;
        label.textContent = `${selected.size} selected`;
    },
    clearSelection(kind) {
        const selected = kind === 'repos' ? this.selectedRepos : this.selectedChats;
        selected.clear();
        this.updateBulk(kind);
        if (kind === 'repos') this.pollData();
        else this.pollChats();
    },
    async openAssign(kind) {
        this.assignTarget = kind;
        await this.refreshTagEditor();
        const list = document.getElementById('assignTagList');
        list.innerHTML = this.tags.map(tag => `
            <label class="tag-editor-row"><input type="checkbox" value="${tag.id}"> <span>${this.escape(tag.name)}</span></label>
        `).join('');
    },
    async pollChats() {
        const res = await fetch('/api/chats/overview');
        const data = await res.json();
        this.renderChatProgress(data);
        this.renderChatSummary(data);
        this.renderHeatmap(data.heatmap || []);
        this.renderTagStats(data.tag_counts || []);
        this.renderChatList(data.chats || []);
    },
    renderChatProgress(data) {
        const block = document.getElementById('chatProgress');
        if (!data.retag_in_progress || !data.retag_total) {
            block.hidden = true;
            return;
        }
        block.hidden = false;
        const pct = Math.round((data.retag_done / data.retag_total) * 100);
        document.getElementById('chatProgressBar').style.width = pct + '%';
        document.getElementById('chatProgressLabel').textContent = `${pct}% · ${data.retag_done}/${data.retag_total} tagged`;
    },
    renderChatSummary(data) {
        const sources = Object.entries(data.sources || {}).map(([name, count]) => `${count} ${name}`).join(' · ') || 'no source';
        const closures = Object.entries(data.closures || {}).map(([name, count]) => `${count} ${name}`).join(' · ');
        const count = data.total || 0;
        const noun = count === 1 ? 'conversation' : 'conversations';
        document.getElementById('chatSummary').textContent = `${count} ${noun} · ${sources} · dated ${data.dated || 0} · undated ${data.undated || 0}${closures ? ' · ' + closures : ''}`;
    },
    renderHeatmap(days) {
        const host = document.getElementById('chatHeatmap');
        if (!days.length) {
            host.innerHTML = '';
            return;
        }
        const max = Math.max(1, ...days.map(day => day.count));
        host.innerHTML = days.map(day => {
            const level = day.count === 0 ? 0 : Math.min(4, Math.ceil((day.count / max) * 4));
            return `<span class="heat-cell level-${level}" title="${day.date}: ${day.count}"></span>`;
        }).join('');
    },
    renderTagStats(counts) {
        const host = document.getElementById('chatTagStats');
        host.innerHTML = counts.map(tag => `<div class="tag-stat"><strong>${tag.count}</strong> <span>${this.escape(tag.name)}</span></div>`).join('');
    },
    renderChatList(chats) {
        const c = document.getElementById('chatContainer');
        const seen = new Set();
        chats.forEach(chat => {
            const id = String(chat.id);
            seen.add(id);
            let card = c.querySelector(`[data-chat-id="${id}"]`);
            if (!card) {
                card = document.createElement('div');
                card.className = 'card';
                card.dataset.chatId = id;
                card.innerHTML = `
                    <label class="card-check"><input type="checkbox"></label>
                    <h3 data-field="title"></h3>
                    <div class="chip-row" data-field="meta"></div>
                    <div class="chip-row" data-field="tags"></div>
                    <div class="card-desc" data-field="summary"></div>
                    <button class="btn outline full read-btn">Read</button>
                `;
                card.querySelector('input').addEventListener('change', (event) => {
                    if (event.target.checked) this.selectedChats.add(id);
                    else this.selectedChats.delete(id);
                    this.updateBulk('chats');
                });
                card.querySelector('.read-btn').addEventListener('click', () => this.openChat(id));
                c.appendChild(card);
            }
            card.querySelector('[data-field="title"]').textContent = chat.title || 'Imported Chat';
            card.querySelector('[data-field="meta"]').innerHTML = `
                <span class="badge">${this.escape(chat.source || '')}</span>
                <span class="badge">${this.escape(chat.created_on || 'undated')}</span>
                <span class="badge">${this.escape(chat.closure_reason || '')}</span>
            `;
            card.querySelector('[data-field="tags"]').innerHTML = (chat.tags || []).map(name => `<span class="badge">${this.escape(name)}</span>`).join('');
            card.querySelector('[data-field="summary"]').textContent = chat.summary || '';
            card.querySelector('input').checked = this.selectedChats.has(id);
        });
        c.querySelectorAll('[data-chat-id]').forEach(card => {
            if (!seen.has(card.dataset.chatId)) card.remove();
        });
    },
    async openChat(id) {
        const res = await fetch(`/api/chats/${encodeURIComponent(id)}`);
        const data = await res.json();
        document.getElementById('readerTitle').textContent = data.title || 'Conversation';
        document.getElementById('readerMeta').textContent = `${data.source || ''} · ${data.created_on || 'undated'} · ${data.closure_reason || ''}`;
        document.getElementById('readerSummary').textContent = data.summary || '';
        document.getElementById('readerTags').innerHTML = (data.visible_tags || []).map(name => `<span class="badge">${this.escape(name)}</span>`).join('');
        document.getElementById('readerText').textContent = data.text || '';
        document.getElementById('chatReaderModal').style.display = 'flex';
    },
    async retagChats() {
        document.getElementById('chatStatus').innerText = 'Recalculating tags...';
        await fetch('/api/chats/retag', { method: 'POST' });
        this.pollChats();
    },
    async generateTimeTravel(repoId, event) {
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
        btns.style.display = 'none';
        
        html2canvas(modal, { backgroundColor: '#0a0a0a', scale: 2 }).then(canvas => {
            const link = document.createElement('a');
            link.download = 'graveyard_analytics.png';
            link.href = canvas.toDataURL('image/png');
            link.click();
            btns.style.display = 'flex';
        });
    },
    escape(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    }
};
document.addEventListener('DOMContentLoaded', () => app.init());
