import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

const EMOTION_EXPRESSIONS = {
    normal: 'neutral',
    thinking: 'relaxed',
    happy: 'happy',
    sad: 'sad',
    angry: 'angry',
    surprised: 'surprised',
};

export class VRMRenderer {
    constructor(canvas, config) {
        this.canvas = canvas;
        this.modelUrl = config.vrm_model_url;
        this.ready = false;
        this.isSpeaking = false;
        this.time = 0;
        this.nextBlink = 2.5;
        this.blinkElapsed = -1;
        this.expression = 'neutral';
        this.choreography = new window.CharacterMotion(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
        this.wings = [];
        this.bones = [];
        this.tempEuler = new THREE.Euler();
        this.tempQuaternion = new THREE.Quaternion();
        this._onResize = () => this.resize();
        window.addEventListener('resize', this._onResize);
    }

    async load() {
        if (!this.modelUrl) throw new Error('VRM model path is not configured');

        this.renderer = new THREE.WebGLRenderer({
            canvas: this.canvas,
            alpha: true,
            antialias: true,
            powerPreference: 'low-power',
        });
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
        this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        this.renderer.setClearColor(0x000000, 0);

        this.scene = new THREE.Scene();
        this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8b80a8, 2.2));
        const keyLight = new THREE.DirectionalLight(0xffffff, 2.0);
        keyLight.position.set(1.5, 2.5, 3);
        this.scene.add(keyLight);

        this.camera = new THREE.PerspectiveCamera(30, 1, 0.05, 100);
        const loader = new GLTFLoader();
        loader.register((parser) => new VRMLoaderPlugin(parser));
        const gltf = await loader.loadAsync(this.modelUrl);
        const vrm = gltf.userData.vrm;
        if (!vrm) throw new Error('The selected file is not a VRM avatar');

        this.vrm = vrm;
        VRMUtils.rotateVRM0(vrm);
        VRMUtils.removeUnnecessaryVertices(gltf.scene);
        this.scene.add(vrm.scene);
        vrm.scene.updateMatrixWorld(true);

        for (const name of ['head', 'chest', 'leftUpperArm', 'rightUpperArm', 'leftLowerArm', 'leftHand']) {
            const node = vrm.humanoid.getNormalizedBoneNode(name);
            if (node) this.bones.push({ name, node, rest: node.quaternion.clone() });
        }
        vrm.scene.traverse(node => {
            if (/^Dragon_Wing_/.test(node.name)) this.wings.push({node, scale: node.scale.clone()});
        });

        this.bounds = new THREE.Box3().setFromObject(vrm.scene);
        this.center = this.bounds.getCenter(new THREE.Vector3());
        this.size = this.bounds.getSize(new THREE.Vector3());
        this.resize();
        this.ready = true;
        return true;
    }

    resize() {
        if (!this.renderer || !this.camera) return;
        const width = Math.max(1, this.canvas.clientWidth || window.innerWidth);
        const height = Math.max(1, this.canvas.clientHeight || window.innerHeight);
        this.renderer.setSize(width, height, false);
        this.camera.aspect = width / height;
        this.camera.updateProjectionMatrix();
        if (this.bounds) this.fitCamera();
    }

    fitCamera() {
        const halfFov = THREE.MathUtils.degToRad(this.camera.fov * 0.5);
        const verticalDistance = this.size.y / (2 * Math.tan(halfFov));
        const horizontalDistance = this.size.x / (2 * Math.tan(halfFov) * this.camera.aspect);
        const distance = Math.max(verticalDistance, horizontalDistance) * 1.16;
        this.camera.position.set(this.center.x, this.center.y, this.center.z + distance);
        this.camera.lookAt(this.center);
    }

    setAction(action) {
        if (!this.ready || !action) return;
        this.choreography.setAction(action);
        const requested = EMOTION_EXPRESSIONS[action.expression] || action.expression || 'neutral';
        this.expression = this.vrm.expressionManager.getExpression(requested) ? requested : 'neutral';
    }

    setSpeaking(value) {
        this.isSpeaking = !!value;
    }

    updateBlink(dt, eyeOpen) {
        if (this.time >= this.nextBlink && this.blinkElapsed < 0) {
            this.blinkElapsed = 0;
            this.nextBlink = this.time + 2.5 + Math.random() * 3;
        }

        let automaticBlink = 0;
        if (this.blinkElapsed >= 0) {
            this.blinkElapsed += dt;
            const phase = this.blinkElapsed / 0.18;
            automaticBlink = phase < 0.5 ? phase * 2 : (1 - phase) * 2;
            if (phase >= 1) {
                this.blinkElapsed = -1;
                automaticBlink = 0;
            }
        }

        return Math.max(automaticBlink, 1 - THREE.MathUtils.clamp(eyeOpen, 0, 1));
    }

    updateIdle(pose) {

        for (const bone of this.bones) {
            let pitch = 0;
            let yaw = 0;
            let roll = 0;
            if (bone.name === 'head') {
                pitch = pose.headPitch;
                yaw = pose.headYaw;
                roll = -pose.roll * 0.7;
            } else if (bone.name === 'leftUpperArm') {
                roll = -1.15 + pose.armLift;
                pitch = -pose.armLift * 0.12;
            } else if (bone.name === 'rightUpperArm') {
                roll = 1.15;
            } else if (bone.name === 'leftLowerArm') {
                roll = pose.armLift * 1.2;
                yaw = pose.armWave * 0.35;
            } else if (bone.name === 'leftHand') {
                roll = pose.armWave * 0.18;
            } else {
                pitch = pose.bow * 2.5 + pose.pulse;
                roll = -pose.roll * 0.5;
            }
            this.tempEuler.set(pitch, yaw, roll);
            this.tempQuaternion.setFromEuler(this.tempEuler);
            bone.node.quaternion.copy(bone.rest).multiply(this.tempQuaternion);
        }
        for (const wing of this.wings) {
            wing.node.scale.copy(wing.scale);
            wing.node.scale.x *= 1 + pose.flutter * 0.7;
        }
    }

    render(params = {}, dt = 0) {
        if (!this.ready) return;
        this.time += dt;
        const pose = this.choreography.sample(dt, params.mouthOpen, this.isSpeaking);
        const expressions = this.vrm.expressionManager;

        for (const name of ['neutral', 'relaxed', 'happy', 'sad', 'angry', 'surprised']) {
            if (expressions.getExpression(name)) {
                const active = name === this.expression ? Math.max(pose.weight, this.isSpeaking ? 0.65 : 0) : 0;
                const current = expressions.getValue(name) || 0;
                expressions.setValue(name, THREE.MathUtils.lerp(current, active, 1 - Math.exp(-dt * 8)));
            }
        }
        expressions.setValue('aa', pose.mouth);
        expressions.setValue('blink', this.updateBlink(dt, 1 + ((params.eyeOpen ?? 1) - 1) * pose.weight));
        this.updateIdle(pose);

        this.vrm.update(dt);
        this.renderer.render(this.scene, this.camera);
    }

    dispose() {
        window.removeEventListener('resize', this._onResize);
        if (this.vrm) VRMUtils.deepDispose(this.vrm.scene);
        this.renderer?.dispose();
    }
}
