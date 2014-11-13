# patch.py -- For dealing with packed-style patches.
# Copyright (C) 2009-2013 Jelmer Vernooij <jelmer@samba.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# of the License or (at your option) a later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA  02110-1301, USA.

"""Classes for dealing with git am-style patches.

These patches are basically unified diffs with some extra metadata tacked
on.
"""

from io import BytesIO
from difflib import SequenceMatcher
import email.parser
import time

from dulwich.objects import (
    Commit,
    S_ISGITLINK,
    )

FIRST_FEW_BYTES = 8000


def write_commit_patch(f, commit, contents, progress, version=None):
    """Write a individual file patch.

    :param commit: Commit object
    :param progress: Tuple with current patch number and total.
    :return: tuple with filename and contents
    """
    (num, total) = progress
    f.write(b'From ' + commit.id + b' ' + time.ctime(commit.commit_time).encode('ascii') + b'\n')
    f.write(b'From: ' + commit.author + b'\n')
    f.write(b'Date: ' + time.strftime("%a, %d %b %Y %H:%M:%S %Z").encode('ascii') + b'\n')
    f.write(('Subject: [PATCH %d/%d] ' % (num, total)).encode('ascii') + commit.message + b'\n')
    f.write(b'\n')
    f.write(b'---\n')
    try:
        import subprocess
        p = subprocess.Popen(["diffstat"], stdout=subprocess.PIPE,
                             stdin=subprocess.PIPE)
    except (ImportError, OSError):
        pass # diffstat not available?
    else:
        (diffstat, _) = p.communicate(contents)
        f.write(diffstat)
        f.write("\n")
    f.write(contents)
    f.write(b'-- \n')
    if version is None:
        from dulwich import __version__ as dulwich_version
        f.write(("Dulwich %d.%d.%d\n" % dulwich_version).encode('ascii'))
    else:
        f.write(version + b'\n')


def get_summary(commit):
    """Determine the summary line for use in a filename.

    :param commit: Commit
    :return: Summary string
    """
    return commit.message.splitlines()[0].replace(b' ', b'-')


def unified_diff(a, b, fromfile=b'', tofile=b'', n=3):
    """difflib.unified_diff that doesn't write any dates or trailing spaces.

    Based on the same function in Python2.6.5-rc2's difflib.py
    """
    started = False
    for group in SequenceMatcher(None, a, b).get_grouped_opcodes(n):
        if not started:
            yield b'--- ' + fromfile + b'\n'
            yield b'+++ ' + tofile + b'\n'
            started = True
        i1, i2, j1, j2 = group[0][1], group[-1][2], group[0][3], group[-1][4]
        yield ("@@ -%d,%d +%d,%d @@\n" % (i1+1, i2-i1, j1+1, j2-j1)).encode('ascii')
        for tag, i1, i2, j1, j2 in group:
            if tag == 'equal':
                for line in a[i1:i2]:
                    yield b' ' + line
                continue
            if tag == 'replace' or tag == 'delete':
                for line in a[i1:i2]:
                    if not line[-1:] == b'\n':
                        line += b'\n\\ No newline at end of file\n'
                    yield b'-' + line
            if tag == 'replace' or tag == 'insert':
                for line in b[j1:j2]:
                    if not line[-1:] == b'\n':
                        line += b'\n\\ No newline at end of file\n'
                    yield b'+' + line


def is_binary(content):
    """See if the first few bytes contain any null characters.

    :param content: Bytestring to check for binary content
    """
    return b'\0' in content[:FIRST_FEW_BYTES]


def write_object_diff(f, store, old_file, new_file, diff_binary=False):
    """Write the diff for an object.

    :param f: File-like object to write to
    :param store: Store to retrieve objects from, if necessary
    :param old_file: (path, mode, hexsha) tuple
    :param new_file: (path, mode, hexsha) tuple
    :param diff_binary: Whether to diff files even if they
        are considered binary files by is_binary().

    :note: the tuple elements should be None for nonexistant files
    """
    (old_path, old_mode, old_id) = old_file
    (new_path, new_mode, new_id) = new_file
    def shortid(hexsha):
        if hexsha is None:
            return b'0' * 7
        else:
            return hexsha[:7]

    def content(mode, hexsha):
        if hexsha is None:
            return b''
        elif S_ISGITLINK(mode):
            return b'Submodule commit ' + hexsha + b'\n'
        else:
            return store[hexsha].data

    def lines(content):
        if not content:
            return []
        else:
            return content.splitlines(True)

    if old_path is None:
        old_path = b'/dev/null'
    else:
        old_path = b'a/' + old_path
    if new_path is None:
        new_path = b'/dev/null'
    else:
        new_path = b'b/' + new_path
    f.write(b'diff --git ' + old_path + b' ' + new_path + b'\n')
    if old_mode != new_mode:
        if new_mode is not None:
            if old_mode is not None:
                f.write(("old mode %o\n" % old_mode).encode('ascii'))
            f.write(("new mode %o\n" % new_mode).encode('ascii'))
        else:
            f.write(("deleted mode %o\n" % old_mode).encode('ascii'))
    f.write(b'index ' + shortid(old_id) + b'..' + shortid(new_id))
    if new_mode is not None:
        f.write((" %o" % new_mode).encode('ascii'))
    f.write(b'\n')
    old_content = content(old_mode, old_id)
    new_content = content(new_mode, new_id)
    if not diff_binary and (is_binary(old_content) or is_binary(new_content)):
        f.write(b'Binary files ' + old_path + b' and ' + new_path + b' differ\n')
    else:
        f.writelines(unified_diff(lines(old_content), lines(new_content),
                                  old_path, new_path))


def write_blob_diff(f, old_file, new_file):
    """Write diff file header.

    :param f: File-like object to write to
    :param old_file: (path, mode, hexsha) tuple (None if nonexisting)
    :param new_file: (path, mode, hexsha) tuple (None if nonexisting)

    :note: The use of write_object_diff is recommended over this function.
    """
    # TODO Avoid so much encode/decode
    (old_path, old_mode, old_blob) = old_file
    (new_path, new_mode, new_blob) = new_file
    def blob_id(blob):
        if blob is None:
            return b'0' * 7
        else:
            return blob.id[:7]
    def lines(blob):
        if blob is not None:
            return blob.data.splitlines(True)
        else:
            return []
    if old_path is None:
        old_path = b'/dev/null'
    else:
        old_path = b'a/' + old_path
    if new_path is None:
        new_path = b'/dev/null'
    else:
        new_path = b'b/' + new_path
    f.write(b'diff --git ' + old_path + b' ' + new_path + b'\n')
    if old_mode != new_mode:
        if new_mode is not None:
            if old_mode is not None:
                f.write(("old mode %o\n" % old_mode).encode('ascii'))
            f.write(("new mode %o\n" % new_mode).encode('ascii'))
        else:
            f.write(("deleted mode %o\n" % old_mode).encode('ascii'))
    f.write(b'index ' + blob_id(old_blob) + b'..' + blob_id(new_blob))
    if new_mode is not None:
        f.write((" %o" % new_mode).encode('ascii'))
    f.write(b'\n')
    old_contents = lines(old_blob)
    new_contents = lines(new_blob)
    f.writelines(unified_diff(old_contents, new_contents,
                              old_path, new_path))


def write_tree_diff(f, store, old_tree, new_tree, diff_binary=False):
    """Write tree diff.

    :param f: File-like object to write to.
    :param old_tree: Old tree id
    :param new_tree: New tree id
    :param diff_binary: Whether to diff files even if they
        are considered binary files by is_binary().
    """
    changes = store.tree_changes(old_tree, new_tree)
    for (oldpath, newpath), (oldmode, newmode), (oldsha, newsha) in changes:
        write_object_diff(f, store, (oldpath, oldmode, oldsha),
                                    (newpath, newmode, newsha),
                                    diff_binary=diff_binary)


def git_am_patch_split(f):
    """Parse a git-am-style patch and split it up into bits.

    :param f: File-like object to parse
    :return: Tuple with commit object, diff contents and git version
    """
    try:
        parser = email.parser.BytesParser()
    except AttributeError:
        # PY2
        parser = email.parser.Parser()
    msg = parser.parse(f)
    c = Commit()
    c.author = msg["from"].encode('ascii')
    c.committer = msg["from"].encode('ascii')
    try:
        patch_tag_start = msg["subject"].index("[PATCH")
    except ValueError:
        subject = msg["subject"]
    else:
        close = msg["subject"].index("] ", patch_tag_start)
        subject = msg["subject"][close+2:]
    c.message = subject.encode('ascii').replace(b'\n', b'') + b'\n'
    first = True

    body = BytesIO(msg.get_payload(decode=True))

    for l in body:
        if l == b'---\n':
            break
        if first:
            if l.startswith(b'From: '):
                c.author = l[len(b'From: '):].rstrip()
            else:
                c.message += b'\n' + l
            first = False
        else:
            c.message += l
    diff = b''
    for l in body:
        if l == b'-- \n':
            break
        diff += l
    try:
        version = next(body).rstrip(b'\n')
    except StopIteration:
        version = None
    return c, diff, version
