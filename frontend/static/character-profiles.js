/* Profiles own performance settings; the stage belongs to the whole studio. */
class CharacterProfiles {
    constructor() {
        this.$ = id => document.getElementById(id);
        this.select = this.$('character-select');
        this.dialog = this.$('new-character-dialog');
        this.select.addEventListener('change', () => this.activate(this.select.value));
        this.$('character-add').addEventListener('click', () => {
            if (!this.canSwitch()) return;
            this.$('new-character-source').textContent = this.context.profiles.find(p=>p.id===this.context.active_id)?.name;
            this.$('new-character-form').reset();
            this.$('new-character-error').textContent = '';
            this.dialog.showModal();
        });
        this.$('new-character-cancel').addEventListener('click', () => this.dialog.close());
        this.$('new-character-form').addEventListener('submit', event => {event.preventDefault(); this.create();});
    }
    update(context) {
        if (!context || this.context?.revision > context.revision) return;
        this.context = context;
        this.select.replaceChildren(...context.profiles.map(profile => {
            const option = document.createElement('option');
            option.value = profile.id;
            option.textContent = profile.name + (profile.id === context.default_id ? ' · 默认' : '');
            return option;
        }));
        this.select.value = context.active_id;
        const active = context.profiles.find(p => p.id === context.active_id);
        this.$('character-voice-summary').textContent = active?.voice_enabled ? `${active.voice_provider} · ${active.voice_name}` : 'Voice off · 语音已关闭';
        this.$('character-default-badge').hidden = context.active_id !== context.default_id;
        this.$('character-count').textContent = `${context.profiles.length} / 24 profiles`;
        this.select.disabled = !!this.busy;
        this.$('character-add').disabled = !!this.busy || context.profiles.length >= 24;
    }
    canSwitch() {
        if (this.busy || !this.context || !window.studioControls?.loaded) return false;
        if (window.studioControls.media?.busy) {
            this.notice('素材正在上传，请完成后再切换角色。',true);return false;
        }
        if (window.studioControls.dirty) {
            this.notice('请先保存或丢弃角色 / 场景草稿，再切换角色。',true);
            this.select.value = this.context.active_id;
            window.studioControls.open(window.studioControls.tab);
            return false;
        }
        return true;
    }
    setBusy(value) {
        this.busy = value; this.update(this.context);
        this.$('new-character-submit').disabled = value;
    }
    async activate(id) {
        if (!this.canSwitch()) {if(this.context)this.select.value=this.context.active_id;return;}
        if (id === this.context.active_id) return;
        this.setBusy(true); this.notice('Switching character · 正在切换…');
        try {
            const result = await window.studioControls.request('/api/characters/activate',{profile_id:id,revision:this.context.revision});
            window.studioControls.adopt(result);
            window.controlPanel?.loadConfig();
            this.notice('Character ready · 已切换，场景与音乐保留');
            return true;
        } catch(error) {
            this.notice(error.message,true);
            await window.studioControls.load();
            return false;
        } finally {this.setBusy(false);}
    }
    async create() {
        if (!this.canSwitch()) return;
        this.setBusy(true);
        try {
            const result = await window.studioControls.request('/api/characters',{
                name:this.$('new-character-name').value.trim(), source_id:this.context.active_id, revision:this.context.revision
            });
            this.update(result.profile_context);
            // Creation and activation are separate: a busy live reply can postpone
            // switching without losing the new profile that was just saved.
            this.dialog.close(); this.setBusy(false);
            if (await this.activate(result.id)) window.studioControls.open('identity');
        } catch(error) {this.$('new-character-error').textContent=error.message;}
        finally {this.setBusy(false);}
    }
    notice(message,error=false) {
        this.$('character-switch-status').textContent=message;
        this.$('character-switch-status').classList.toggle('error',error);
    }
}
window.addEventListener('DOMContentLoaded', () => {window.characterProfiles=new CharacterProfiles();});
