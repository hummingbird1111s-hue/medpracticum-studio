"""
Chart helpers for the report builder.

CRITICAL: matplotlib, numpy, pandas, and seaborn are NOT imported at
module level. They consume ~100 MB of RAM just from import overhead
(docstrings, fonts, extensions) and cause Gunicorn workers to be
OOM-killed on Render's free tier. All heavy imports happen lazily
inside the chart functions.
"""

import base64
import io
import logging

logger = logging.getLogger(__name__)


def _get_plt():
    """Return matplotlib.pyplot with the Agg backend. Loads on first call."""
    import matplotlib
    matplotlib.use('Agg', force=True)
    import matplotlib.pyplot as plt
    return plt


def _png_data_uri(fig):
    """Render a matplotlib figure to a base64 PNG data URI."""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=110, bbox_inches='tight')
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode('ascii')
    try:
        import matplotlib.pyplot as plt
        plt.close(fig)
    except Exception:
        pass
    return 'data:image/png;base64,' + data


def bar_chart(labels, values, ylabel='Count', horizontal=False):
    """Build a bar chart as a base64 data URI."""
    try:
        plt = _get_plt()

        labels = [str(l) for l in labels]
        values = [float(v) for v in values]

        # Size the figure sensibly based on count
        n = max(len(labels), 1)
        if horizontal:
            fig, ax = plt.subplots(figsize=(7, max(2.5, n * 0.45)))
            bars = ax.barh(labels, values, color='#047857')
            ax.invert_yaxis()
            ax.set_xlabel(ylabel)
            for bar, v in zip(bars, values):
                ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2,
                        f' {v:g}', va='center', fontsize=9)
        else:
            fig, ax = plt.subplots(figsize=(7, 3.6))
            bars = ax.bar(labels, values, color='#047857')
            ax.set_ylabel(ylabel)
            ax.tick_params(axis='x', rotation=35)
            for bar, v in zip(bars, values):
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                        f'{v:g}', ha='center', va='bottom', fontsize=9)

        ax.set_title(ylabel, fontsize=11, loc='left')
        for spine in ('top', 'right'):
            ax.spines[spine].set_visible(False)
        fig.tight_layout()
        return _png_data_uri(fig)
    except Exception as exc:
        logger.exception('bar_chart failed: %s', exc)
        return ''


def line_chart(labels, values, ylabel='Value'):
    """Build a line chart as a base64 data URI."""
    try:
        plt = _get_plt()

        labels = [str(l) for l in labels]
        values = [float(v) for v in values]

        fig, ax = plt.subplots(figsize=(7, 3.4))
        ax.plot(labels, values, marker='o', color='#047857', linewidth=2,
                markersize=6, markerfacecolor='#10b981')
        ax.set_ylabel(ylabel)
        ax.tick_params(axis='x', rotation=35)
        ax.grid(True, alpha=0.25, linestyle='--')
        for spine in ('top', 'right'):
            ax.spines[spine].set_visible(False)
        fig.tight_layout()
        return _png_data_uri(fig)
    except Exception as exc:
        logger.exception('line_chart failed: %s', exc)
        return ''


def qr_data_uri(url):
    """PNG QR code as a base64 data URI."""
    try:
        import qrcode
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        logger.warning('qrcode lib not installed; PDF renders without QR')
        return ''
    except Exception:
        logger.exception('QR generation failed')
        return ''