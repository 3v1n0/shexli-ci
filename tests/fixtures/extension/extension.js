// shexli-ci: EGO-I-004 - the fixture deliberately uses the legacy import
const Gi = imports._gi;

export default class TestExtension {
    enable() {
        return Gi;
    }

    disable() {
        this._gi = null;
    }
}
