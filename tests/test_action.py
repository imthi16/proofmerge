"""Exercise the composite action's actual scripts offline with fake executables."""

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACTION = (ROOT / 'action.yml').read_text()
STEPS = re.split(r'^    - ', ACTION, flags=re.MULTILINE)[1:]


def step(label):
    return next(part for part in STEPS if part.splitlines()[0] == label)


def script(label):
    return textwrap.dedent(step(label).split('      run: |\n', 1)[1])


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    # Fake gh records argv and file contents; it never opens a network connection.
    gh = bin_dir / 'gh'
    gh.write_text(f'#!{sys.executable}\n' + textwrap.dedent('''\
        import json
        import os
        import sys
        from pathlib import Path

        args = sys.argv[1:]
        entry = {'args': args}
        for option in ('--body-file', '--input'):
            if option in args:
                entry['body'] = Path(args[args.index(option) + 1]).read_text()
        with open(os.environ['GH_CALLS'], 'a') as out:
            out.write(json.dumps(entry) + '\\n')
        if '--paginate' in args:
            print(os.environ.get('COMMENTS', '[[]]'))
        if os.environ.get('GH_FAIL'):
            sys.exit(1)
        '''))
    gh.chmod(0o755)
    python = bin_dir / 'python'
    python.symlink_to(sys.executable)
    return {
        **os.environ,
        'PATH': f'{bin_dir}:{os.environ["PATH"]}',
        'RUNNER_TEMP': str(tmp_path),
        'GITHUB_WORKSPACE': str(tmp_path / 'workspace'),
        'GITHUB_OUTPUT': str(tmp_path / 'output'),
        'GH_CALLS': str(tmp_path / 'gh-calls'),
        'GH_REPO': 'owner/repo',
        'GH_TOKEN': 'fake-token',
        'PR_NUMBER': '42',
        'ARTIFACT_URL': 'https://github.com/owner/repo/actions/runs/1/artifacts/2',
        'FAIL_ON': 'block',
    }


def run(label, env):
    return subprocess.run(
        ['bash', '--noprofile', '--norc', '-e', '-o', 'pipefail', '-c', script(label)],
        cwd=env['RUNNER_TEMP'], env=env, capture_output=True, text=True, check=False,
    )


def proof(env, decision='PASS', reasons=None, code=0):
    temp = Path(env['RUNNER_TEMP'])
    evidence = temp / 'proofmerge-evidence'
    evidence.mkdir(exist_ok=True)
    (evidence / 'proof.json').write_text(json.dumps({
        'verdict': {'decision': decision, 'reasons': reasons or ['tested scope']},
    }))
    (temp / 'proofmerge-exit-code').write_text(str(code))


def calls(env):
    return [json.loads(line) for line in Path(env['GH_CALLS']).read_text().splitlines()]


def test_metadata_and_trusted_install():
    assert 'model-api-key:\n' in ACTION and '    required: true' in ACTION
    assert 'fail-on:\n' in ACTION and '    default: block' in ACTION
    assert "    default: '3.13'" in ACTION
    assert '  using: composite' in ACTION
    assert 'uses: actions/setup-python@v5' in ACTION
    assert 'python-version: ${{ inputs.python-version }}' in ACTION
    assert 'ACTION_PATH: ${{ github.action_path }}' in step('name: Install action code')
    assert 'python -m pip install "$ACTION_PATH"' in script('name: Install action code')
    assert 'docker build -t proofmerge-runner:0.1 "$ACTION_PATH/docker/runner"' in script(
        'name: Build sandbox runner'
    )
    upload = step('uses: actions/upload-artifact@v4')
    assert 'always()' in upload
    assert 'path: ${{ runner.temp }}/proofmerge-evidence' in upload
    labels = [part.splitlines()[0] for part in STEPS]
    assert labels.index('name: Verify') < labels.index('uses: actions/upload-artifact@v4')
    assert labels.index('uses: actions/upload-artifact@v4') < labels.index(
        'name: Post or update verdict'
    ) < labels.index('name: Apply verdict policy')
    unguarded = {'name: Fail when the model key is missing'}
    for part in STEPS[1:]:
        if part.splitlines()[0] in unguarded:
            assert "missing_key == 'fail'" in part
            continue
        assert "steps.credentials.outputs.enabled == 'true'" in part
    assert 'fail-on-missing-key:\n' in ACTION
    missing_key = ACTION.split('fail-on-missing-key:', 1)[1].split('runs:', 1)[0]
    assert "default: 'false'" in missing_key
    assert 'python-root' not in ACTION
    assert '--config' not in ACTION
    for part in STEPS:
        if '      run: |\n' in part:
            assert '${{' not in part.split('      run: |\n')[1]


def test_empty_key_skips_cleanly(env):
    result = run('name: Check credentials', {**env, 'MODEL_API_KEY': ''})
    assert result.returncode == 0
    assert result.stdout == (
        'skipped: no MODEL_API_KEY (fork PR?)\n'
        '::warning::ProofMerge skipped because MODEL_API_KEY is not set. '
        'No verdict was produced.\n'
    )
    output = Path(env['GITHUB_OUTPUT']).read_text()
    assert 'enabled=false\n' in output
    assert 'skipped=true\n' in output
    assert 'missing_key=warn\n' in output
    skipped = json.loads((Path(env['RUNNER_TEMP']) / 'proofmerge-evidence/skipped.json').read_text())
    assert skipped == {
        'status': 'skipped',
        'reason': 'MODEL_API_KEY is not set',
        'verdict': None,
    }
    assert not Path(env['GH_CALLS']).exists()


def test_missing_key_can_fail_the_job(env):
    result = run('name: Check credentials', {
        **env, 'MODEL_API_KEY': '', 'FAIL_ON_MISSING_KEY': 'true',
    })
    assert result.returncode == 0, result.stderr
    assert 'missing_key=fail' in Path(env['GITHUB_OUTPUT']).read_text()
    failed = run('name: Fail when the model key is missing', env)
    assert failed.returncode == 1
    assert 'MODEL_API_KEY is not set' in failed.stdout
    assert 'not a code verdict' in failed.stdout


def test_invalid_missing_key_flag_fails(env):
    result = run('name: Check credentials', {
        **env, 'MODEL_API_KEY': 'fake-secret', 'FAIL_ON_MISSING_KEY': 'yes',
    })
    assert result.returncode == 1
    assert 'fake-secret' not in result.stdout + result.stderr
    assert 'invalid fail-on-missing-key' in result.stderr


def test_present_key_is_not_printed(env):
    result = run('name: Check credentials', {**env, 'MODEL_API_KEY': 'fake-secret'})
    assert result.returncode == 0
    assert 'fake-secret' not in result.stdout + result.stderr
    assert Path(env['GITHUB_OUTPUT']).read_text() == (
        'enabled=true\nskipped=false\nmissing_key=ok\n'
    )


def test_pr_body_is_env_data(env):
    assert 'PR_BODY: ${{ github.event.pull_request.body }}' in step('name: Record PR intent')
    hostile = '$(touch injected); `touch injected`\n"quotes"\n${HOME}\n'
    result = run('name: Record PR intent', {**env, 'PR_BODY': hostile})
    assert result.returncode == 0
    assert (Path(env['RUNNER_TEMP']) / 'proofmerge-intent.txt').read_text() == hostile
    assert not (Path(env['RUNNER_TEMP']) / 'injected').exists()


def test_verify_captures_exit_and_passes_literal_argv(env):
    fake = Path(env['RUNNER_TEMP']) / 'bin/proofmerge'
    fake.write_text(f'#!{sys.executable}\n' + textwrap.dedent('''\
        import json
        import os
        import sys
        from pathlib import Path
        Path(os.environ['RUNNER_TEMP'], 'verify-args').write_text(json.dumps(sys.argv[1:]))
        sys.exit(2)
        '''))
    fake.chmod(0o755)
    hostile = '$(touch injected)'
    result = run('name: Verify', {
        **env, 'BASE_SHA': hostile, 'HEAD_SHA': 'head-sha', 'MODEL_API_KEY': 'fake-key',
    })
    assert result.returncode == 0
    temp = Path(env['RUNNER_TEMP'])
    assert (temp / 'proofmerge-exit-code').read_text() == '2\n'
    assert json.loads((temp / 'verify-args').read_text()) == [
        'verify', env['GITHUB_WORKSPACE'], '--base', hostile, '--head', 'head-sha',
        '--intent-file', str(temp / 'proofmerge-intent.txt'),
        '--evidence-dir', str(temp / 'proofmerge-evidence'),
    ]
    assert not (temp / 'injected').exists()
    verify_step = step('name: Verify')
    assert 'BASE_SHA: ${{ github.event.pull_request.base.sha }}' in verify_step
    assert 'HEAD_SHA: ${{ github.event.pull_request.head.sha }}' in verify_step
    assert 'MODEL_API_KEY: ${{ inputs.model-api-key }}' in verify_step


@pytest.mark.parametrize(('decision', 'code', 'policy', 'expected'), [
    ('PASS', 0, 'block', 0), ('PASS', 0, 'review', 0),
    ('REVIEW', 0, 'block', 0), ('REVIEW', 0, 'review', 1),
    ('BLOCK', 2, 'block', 1), ('BLOCK', 2, 'review', 1),
    ('PASS', 1, 'block', 1), ('BLOCK', 0, 'block', 1),
    ('REVIEW', 2, 'block', 1), ('PASS', 0, 'invalid', 1),
])
def test_verdict_policy(env, decision, code, policy, expected):
    proof(env, decision, code=code)
    result = run('name: Post or update verdict', {**env, 'FAIL_ON': policy})
    assert result.returncode == 0, result.stderr
    assert run('name: Apply verdict policy', env).returncode == expected


@pytest.mark.parametrize('broken', ['missing', 'malformed', 'unknown', 'reasons', 'exit-code'])
def test_unusable_evidence_fails_and_comments(env, broken):
    temp = Path(env['RUNNER_TEMP'])
    if broken != 'missing':
        proof(env)
        target = temp / 'proofmerge-evidence/proof.json'
        if broken == 'malformed':
            target.write_text('{bad json')
        elif broken == 'unknown':
            proof(env, 'UNKNOWN')
        elif broken == 'reasons':
            target.write_text('{"verdict": {"decision": "PASS", "reasons": "bad"}}')
        else:
            (temp / 'proofmerge-exit-code').write_text('garbage')
    result = run('name: Post or update verdict', env)
    assert result.returncode == 0, result.stderr
    assert 'ProofMerge: ERROR' in calls(env)[1]['body']
    assert run('name: Apply verdict policy', env).returncode == 1


def test_comment_creation_uses_body_file_and_literal_reasons(env):
    hostile = '$(touch injected) `touch injected`\n<!-- proofmerge --> @someone <script>'
    proof(env, reasons=[hostile])
    result = run('name: Post or update verdict', env)
    assert result.returncode == 0, result.stderr
    posted = calls(env)[1]
    assert posted['args'][:3] == ['pr', 'comment', '42']
    assert '--body-file' in posted['args']
    assert hostile not in posted['body']
    assert ''.join(f'&#{ord(char)};' for char in hostile) in posted['body']
    assert posted['body'].count('<!-- proofmerge -->') == 1
    assert env['ARTIFACT_URL'] in posted['body']
    assert not (Path(env['RUNNER_TEMP']) / 'injected').exists()
    assert 'GH_TOKEN: ${{ github.token }}' in step('name: Post or update verdict')


def test_updates_only_bot_marker_comment_across_pages(env):
    proof(env, 'REVIEW')
    comments = [[{'id': 1, 'user': {'login': 'someone'}, 'body': '<!-- proofmerge -->'}], [
        {'id': 2, 'user': {'login': 'github-actions[bot]'}, 'body': 'other comment'},
        {'id': 3, 'user': {'login': 'github-actions[bot]'}, 'body': '<!-- proofmerge --> old'},
    ]]
    result = run('name: Post or update verdict', {**env, 'COMMENTS': json.dumps(comments)})
    assert result.returncode == 0, result.stderr
    updated = calls(env)[1]
    assert updated['args'][:4] == ['api', '--method', 'PATCH', 'repos/owner/repo/issues/comments/3']
    assert '--input' in updated['args']
    assert 'ProofMerge: REVIEW' in json.loads(updated['body'])['body']
    assert len(calls(env)) == 2


def test_no_artifact_link_and_comment_failure(env):
    proof(env)
    result = run('name: Post or update verdict', {**env, 'ARTIFACT_URL': '', 'GH_FAIL': '1'})
    assert result.returncode != 0
    body = (Path(env['RUNNER_TEMP']) / 'proofmerge-comment.md').read_text()
    assert 'Evidence artifact unavailable.' in body


def test_gate_fails_if_status_never_written(env):
    assert run('name: Apply verdict policy', env).returncode == 1


def test_documented_workflow_and_limits():
    doc = (ROOT / 'docs/github-action.md').read_text()
    for required in (
        'on: pull_request', 'contents: read', 'pull-requests: write',
        'actions/checkout@v4', 'fetch-depth: 0', 'uses: imthi16/proofmerge@main',
        'Docker is required', 'Linux', 'synthetic, owned', 'no safety claim for arbitrary code',
    ):
        assert required in doc
