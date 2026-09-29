import base64
import io
import textwrap

import matplotlib

matplotlib.use('Agg')  # draw to memory, no window
import matplotlib.pyplot as plt  # noqa: E402

GREEN = '#059669'

plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Times New Roman', 'Liberation Serif', 'DejaVu Serif'],
    'axes.spines.top': False,
    'axes.spines.right': False,
    'font.size': 10,
})


def _to_data_uri(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=200, bbox_inches='tight')
    plt.close(fig)
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')


def _wrap(labels, width=22):
    return [textwrap.fill(str(label), width) for label in labels]


def bar_chart(labels, values, value_label='', horizontal=False):
    labels = _wrap(labels)
    if horizontal:
        fig, ax = plt.subplots(figsize=(6.2, max(2.8, 0.5 * len(labels) + 1)))
        bars = ax.barh(labels, values, color=GREEN)
        ax.invert_yaxis()
        ax.set_xlabel(value_label)
    else:
        fig, ax = plt.subplots(figsize=(6.2, 3.6))
        bars = ax.bar(labels, values, color=GREEN)
        ax.set_ylabel(value_label)
        plt.setp(ax.get_xticklabels(), rotation=30, ha='right')
    ax.bar_label(bars, padding=2)
    return _to_data_uri(fig)


def line_chart(labels, values, value_label=''):
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    ax.plot(labels, values, marker='o', color=GREEN, linewidth=2)
    for x, y in zip(labels, values):
        ax.annotate(f'{y:g}', (x, y), textcoords='offset points', xytext=(0, 6), ha='center', fontsize=9)
    ax.set_ylabel(value_label)
    ax.set_ylim(bottom=0)
    return _to_data_uri(fig)


def qr_data_uri(url):
    import qrcode

    img = qrcode.make(url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')