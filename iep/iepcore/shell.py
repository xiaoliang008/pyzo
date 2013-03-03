# -*- coding: utf-8 -*-
# Copyright (C) 2012, the IEP development team
#
# IEP is distributed under the terms of the (new) BSD License.
# The full license can be found in 'license.txt'.


""" Module shell

Defines the shell to be used in IEP.
This is done in a few inheritance steps:
  - BaseShell inherits BaseTextCtrl and adds the typical shell behaviour.    
  - PythonShell makes it specific to Python.
This module also implements ways to communicate with the shell and to run
code in it.

"""

from iep.codeeditor.qt import QtCore, QtGui
Qt = QtCore.Qt

import os, sys, time, subprocess
import yoton
import iep
from pyzolib import ssdf

from iep.codeeditor.highlighter import Highlighter
from iep.codeeditor import parsers

from iep.iepcore.baseTextCtrl import BaseTextCtrl
from iep.iepcore.iepLogging import print
from iep.iepcore.kernelbroker import KernelInfo, Kernelmanager
from iep.iepcore.menu import ShellContextMenu



# Interval for polling messages. Timer for each kernel. I found
# that this one does not affect performance much
POLL_TIMER_INTERVAL = 30 # 30 ms 33Hz

# Maximum number of lines in the shell
MAXBLOCKCOUNT = iep.config.advanced.shellMaxLines


# todo: we could make command shells to, with autocompletion and coloring...

class YotonEmbedder(QtCore.QObject):
    """ Embed the Yoton event loop.
    """
    
    def __init__(self):
        QtCore.QObject.__init__(self)
        yoton.app.embed_event_loop(self.postYotonEvent)
    
    def postYotonEvent(self):
        try:
            QtGui.qApp.postEvent(self, QtCore.QEvent(QtCore.QEvent.User))
        except Exception:
            pass # If IEP is shutting down, the app may be None
    
    def customEvent(self, event):
        """ This is what gets called by Qt.
        """
        yoton.process_events(False)

yotonEmbedder = YotonEmbedder()


# Short constants for cursor movement
A_KEEP = QtGui.QTextCursor.KeepAnchor
A_MOVE = QtGui.QTextCursor.MoveAnchor

# Instantiate a local kernel broker upon loading this module
iep.localKernelManager = Kernelmanager(public=False)


def finishKernelInfo(info, scriptFile=None):
    """ finishKernelInfo(info, scriptFile=None)
    
    Get a copy of the kernel info struct, with the scriptFile
    and the projectPath set.
    
    """ 

    # Set executable to default if it is empty
    # Note that we do this on the original struct object.
    if not info.exe:
        info.exe = '[default]'
    
    # Make a copy, we do not want to change the original
    info = ssdf.copy(info)
    
    # Set scriptFile (if '', the kernel will run in interactive mode)
    if scriptFile:
        info.scriptFile = scriptFile
    else:
        info.scriptFile = ''
    
    #If the file browser is active, and has the check box
    #'add path to Python path' set, set the PROJECTPATH variable
    fileBrowser = iep.toolManager.getTool('iepfilebrowser')
    if fileBrowser:
        info.projectPath = fileBrowser.getAddToPythonPath()
    else:
        info.projectPath = ''
        
        # Only process project manager tool if file browser did not set a path.
        
        #If the project manager is active, and has the check box
        #'add path to Python path' set, set the PROJECTPATH variable
        projectManager = iep.toolManager.getTool('iepprojectmanager')
        if projectManager:
            info.projectPath = projectManager.getAddToPythonPath()
        else:
            info.projectPath = ''
    
    return info



class ShellHighlighter(Highlighter):
    """ This highlighter implements highlighting for a shell;
    only the input lines are highlighted with this highlighter.
    """
    
    def highlightBlock(self, line): 
        
        t0 = time.time()
        
        # Make sure this is a Unicode Python string
        line = str(line)
        
        # Get previous state
        previousState = self.previousBlockState()
        
        # Get parser
        parser = None
        if hasattr(self._codeEditor, 'parser'):
            parser = self._codeEditor.parser()
        
        # Get function to get format
        nameToFormat = self._codeEditor.getStyleElementFormat
        
        # Last line?
        cursor1 = self._codeEditor._cursor1
        cursor2 = self._codeEditor._cursor2
        commandCursor = self._codeEditor._lastCommandCursor
        curBlock = self.currentBlock()
        #
        atLastPrompt, atCurrentPrompt = False, False
        if curBlock.position() == 0:
            pass
        elif curBlock.position() == commandCursor.block().position():
            atLastPrompt = True
        elif curBlock.position() >= cursor1.block().position():
            atCurrentPrompt = True
        
        
        if (atLastPrompt or atCurrentPrompt) and parser:
            if atCurrentPrompt:
                pos1, pos2 = cursor1.positionInBlock(), cursor2.positionInBlock()
            else:
                pos1, pos2 = 0, commandCursor.positionInBlock()
            
            self.setCurrentBlockState(0)
            for token in parser.parseLine(line, previousState):
                # Handle block state
                if isinstance(token, parsers.BlockState):
                    self.setCurrentBlockState(token.state)
                else:
                    # Get format
                    try:
                        format = nameToFormat(token.name).textCharFormat
                    except KeyError:
                        #print(repr(nameToFormat(token.name)))
                        continue
                    # Set format                    
                    #format.setFontWeight(99)
                    if token.start >= pos2:
                        self.setFormat(token.start,token.end-token.start,format)
                
            # Set prompt to bold
            if atCurrentPrompt:
                format = QtGui.QTextCharFormat()
                format.setFontWeight(99)
                self.setFormat(pos1, pos2-pos1 ,format)
        
        #Get the indentation setting of the editors
        indentUsingSpaces = self._codeEditor.indentUsingSpaces()
        
        # Get user data
        bd = self.getCurrentBlockUserData()
        
        leadingWhitespace=line[:len(line)-len(line.lstrip())]
        if '\t' in leadingWhitespace and ' ' in leadingWhitespace:
            #Mixed whitespace
            bd.indentation = 0
            format=QtGui.QTextCharFormat()
            format.setUnderlineStyle(QtGui.QTextCharFormat.SpellCheckUnderline)
            format.setUnderlineColor(QtCore.Qt.red)
            format.setToolTip('Mixed tabs and spaces')
            self.setFormat(0,len(leadingWhitespace),format)
        elif ('\t' in leadingWhitespace and indentUsingSpaces) or \
            (' ' in leadingWhitespace and not indentUsingSpaces):
            #Whitespace differs from document setting
            bd.indentation = 0
            format=QtGui.QTextCharFormat()
            format.setUnderlineStyle(QtGui.QTextCharFormat.SpellCheckUnderline)
            format.setUnderlineColor(QtCore.Qt.blue)
            format.setToolTip('Whitespace differs from document setting')
            self.setFormat(0,len(leadingWhitespace),format)
        else:
            # Store info for indentation guides
            # amount of tabs or spaces
            bd.indentation = len(leadingWhitespace)



class BaseShell(BaseTextCtrl):
    """ The BaseShell implements functionality to make a generic shell.
    """

    
    def __init__(self, parent,**kwds):
        super().__init__(parent, wrap=True, showLineNumbers=False, 
            highlightCurrentLine=False, parser='python', **kwds)
        
        # Use a special highlighter that only highlights the input.
        self._setHighlighter(ShellHighlighter)
        
        # No undo in shells
        self.setUndoRedoEnabled(False)
        
        # variables we need
        self._more = False
        
        # We use two cursors to keep track of where the prompt is
        # cursor1 is in front, and cursor2 is at the end of the prompt.
        # They can be in the same position.
        # Further, we store a cursor that selects the last given command,
        # so it can be styled.
        self._cursor1 = self.textCursor()
        self._cursor2 = self.textCursor()
        self._lastCommandCursor = self.textCursor()
        
        # When inserting/removing text at the edit line (thus also while typing)
        # keep cursor2 at its place. Only when text is written before
        # the prompt (i.e. in write), this flag is temporarily set to False. 
        # Same for cursor1, because sometimes (when there is no prompt) it
        # is at the same position.
        self._cursor1.setKeepPositionOnInsert(True)
        self._cursor2.setKeepPositionOnInsert(True)
        
        # Similarly, we use the _lastCommandCursor cursor really for pointing.
        self._lastCommandCursor.setKeepPositionOnInsert(True)
        
        # Create the command history.  Commands are added into the
        # front of the list (ie. at index 0) as they are entered.
        self._history = []
        self._historyNeedle = None # None means none, "" means look in all
        self._historyStep = 0
        
        # Set minimum width so 80 lines do fit in smallest font size
        self.setMinimumWidth(200)
        
        # Hard wrapping. QTextEdit allows hard wrapping at a specific column.
        # Unfortunately, QPlainTextEdit does not.
        self.setWordWrapMode(QtGui.QTextOption.WrapAnywhere)
        
        # Limit number of lines
        self.setMaximumBlockCount(MAXBLOCKCOUNT)
        
        # Keep track of position, so we can disable editing if the cursor
        # is before the prompt
        self.cursorPositionChanged.connect(self.onCursorPositionChanged)
    
    
    def onCursorPositionChanged(self):
        #If the end of the selection (or just the cursor if there is no selection)
        #is before the beginning of the line. make the document read-only
        cursor = self.textCursor()
        promptpos = self._cursor2.position()
        if cursor.position() < promptpos or cursor.anchor() < promptpos:
            self.setReadOnly(True)
        else:
            self.setReadOnly(False)
    
    
    def ensureCursorAtEditLine(self):
        """
        If the text cursor is before the beginning of the edit line,
        move it to the end of the edit line
        """
        cursor = self.textCursor()
        promptpos = self._cursor2.position()
        if cursor.position() < promptpos or cursor.anchor() < promptpos:
            cursor.movePosition(cursor.End, A_MOVE) # Move to end of document
            self.setTextCursor(cursor)
            self.onCursorPositionChanged()
    
    
    def mousePressEvent(self, event):
        """ Disable right MB and middle MB (which pastes by default). """
        if event.button() != QtCore.Qt.MidButton:
            BaseTextCtrl.mousePressEvent(self, event)
    
    
    def contextMenuEvent(self, event):
        """ Do not show context menu. """
        pass
    
    
    ##Indentation: override code editor behaviour
    def indentSelection(self):
        pass
    def dedentSelection(self):
        pass
        
    ## Key handlers
    def keyPressEvent(self,event):
        
        if event.key() in [Qt.Key_Return, Qt.Key_Enter]:
            # Enter: execute line
            # Remove calltip and autocomp if shown
            self.autocompleteCancel()
            self.calltipCancel()
            
            # reset history needle
            self._historyNeedle = None
            
            # process
            self.processLine()
            return
        
        if event.key() == Qt.Key_Escape:
            # Escape clears command
            if not ( self.autocompleteActive() or self.calltipActive() ): 
                self.clearCommand()
            
        if event.key() == Qt.Key_Home:
            # Home goes to the prompt.
            cursor = self.textCursor()
            if event.modifiers() & Qt.ShiftModifier:
                cursor.setPosition(self._cursor2.position(), A_KEEP)
            else:
                cursor.setPosition(self._cursor2.position(), A_MOVE)
            #
            self.setTextCursor(cursor)
            self.autocompleteCancel()
            return

        if event.key() == Qt.Key_Insert:
            # Don't toggle between insert mode and overwrite mode.
            return True
        
        #Ensure to not backspace / go left beyond the prompt
        if event.key() in [Qt.Key_Backspace, Qt.Key_Left]:
            self._historyNeedle = None
            if self.textCursor().position() == self._cursor2.position():
                if event.key() == Qt.Key_Backspace:
                    self.textCursor().removeSelectedText()
                return  #Ignore the key, don't go beyond the prompt


        if event.key() in [Qt.Key_Up, Qt.Key_Down] and not \
                self.autocompleteActive():
            
            # needle
            if self._historyNeedle is None:
                # get partly-written-command
                #
                # Select text                
                cursor = self.textCursor()
                cursor.setPosition(self._cursor2.position(), A_MOVE)
                cursor.movePosition(cursor.End, A_KEEP)
                # Update needle text
                self._historyNeedle = cursor.selectedText()
                self._historyStep = 0
            
            #Browse through history
            if event.key() == Qt.Key_Up:
                self._historyStep +=1
            else: # Key_Down
                self._historyStep -=1
                if self._historyStep < 1:
                    self._historyStep = 1
            
            # find the command
            count = 0
            for c in self._history:
                if c.startswith(self._historyNeedle):
                    count+=1
                    if count >= self._historyStep:
                        break
            else:
                # found nothing-> reset
                self._historyStep = 0
                c = self._historyNeedle  
            
            # Replace text
            cursor = self.textCursor()
            cursor.setPosition(self._cursor2.position(), A_MOVE)
            cursor.movePosition(cursor.End, A_KEEP)
            cursor.insertText(c)
            
            self.ensureCursorAtEditLine()
            return
        
        else:
            # Reset needle
            self._historyNeedle = None
        
        #if a 'normal' key is pressed, ensure the cursor is at the edit line
        if event.text():
            self.ensureCursorAtEditLine()
        
        #Default behaviour: BaseTextCtrl
        BaseTextCtrl.keyPressEvent(self,event)
    

    
    ## Cut / Copy / Paste / Drag & Drop
    
    def cut(self):
        """ Reimplement cut to only copy if part of the selected text
        is not at the prompt. """
        
        if self.isReadOnly():
            return self.copy()
        else:
            return BaseTextCtrl.cut(self)
    
    #def copy(self): # no overload needed

    def paste(self):
        """ Reimplement paste to paste at the end of the edit line when
        the position is at the prompt. """
        self.ensureCursorAtEditLine()
        # Paste normally
        return BaseTextCtrl.paste(self)

    def dragEnterEvent(self, event):
        """No dropping allowed"""
        pass
        
    def dropEvent(self,event):
        """No dropping allowed"""
        pass
    
    
    ## Basic commands to control the shell
    
    
    def clearScreen(self):
        """ Clear all the previous output from the screen. """
        # Select from beginning of prompt to start of document
        self._cursor1.clearSelection()
        self._cursor1.movePosition(self._cursor1.Start, A_KEEP) # Keep anchor
        self._cursor1.removeSelectedText()
        # Wrap up
        self.ensureCursorAtEditLine()
        self.ensureCursorVisible()
    
    def deleteLines(self):
        """ Called from the menu option "delete lines", just execute self.clearCommand() """
        self.clearCommand()
        
    def clearCommand(self):
        """ Clear the current command, move the cursor right behind
        the prompt, and ensure it's visible.
        """
        # Select from prompt end to length and delete selected text.
        cursor = self.textCursor()
        cursor.setPosition(self._cursor2.position(), A_MOVE)
        cursor.movePosition(cursor.End, A_KEEP)
        cursor.removeSelectedText()
        # Wrap up
        self.ensureCursorAtEditLine()
        self.ensureCursorVisible()
    
    
    def _handleBackspaces_split(self, text):
        
        # while NOT a backspace at first position, or none found
        i = 9999999999999
        while i>0:
            i = text.rfind('\b',0,i)
            if i>0 and text[i-1]!='\b':
                text = text[0:i-1] + text[i+1:]
        
        # Strip the backspaces at the start
        text2 = text.lstrip('\b')
        n = len(text) - len(text2)
        
        # Done
        return n, text2
    
    
    def _handleBackspacesOnList(self, texts):
        """ _handleBackspacesOnList(texts)
        
        Handle backspaces on a list of messages. When printing
        progress, many messages will simply replace each-other, which
        means we can process them much more effectively than when they're
        combined in a list.
        
        """
        # Init number of backspaces at the start
        N = 0
        
        for i in range(len(texts)):
            
            # Remove backspaces in text and how many are left
            n, text = self._handleBackspaces_split(texts[i])
            texts[i] = text
            
            # Use remaining backspaces to remove backspaces in earlier texts
            while n and i > 0:
                i -= 1
                text = texts[i]
                if len(text) > n:
                    texts[i] = text[:-n]
                    n = 0
                else:
                    texts[i] = ''
                    n -= len(text)
            N += n
        
        # Insert tabs for start
        if N:
            texts[0] = '\b'*N + texts[0]
        
        # Return with empy elements popped
        return [t for t in texts if t]
    
    
    def _handleBackspaces(self, text):
        """ Apply backspaces in the string itself and if there are
        backspaces left at the start of the text, remove the appropriate
        amount of characters from the text.
        
        Returns the new text.
        """
        # take care of backspaces
        if '\b' in text:
            # Remove backspaces and get how many were at the beginning
            nb, text = self._handleBackspaces_split(text)
            if nb:
                # Select what we remove and delete that
                self._cursor1.clearSelection()
                self._cursor1.movePosition(self._cursor1.Left, A_KEEP, nb)
                self._cursor1.removeSelectedText()
        
        # Return result
        return text
    
    
    def _splitLinesForPrinting(self, text):
        """ Given a text, split the text in lines. Lines that are extremely
        long are split in pieces of 80 characters to increase performance for 
        wrapping. This is kind of a failsafe for when the user accidentally
        prints a bitmap or huge list. See issue 98.
        """
        for line in text.splitlines(True):
            if len(line) > 1024: # about 12 lines of 80 chars
                parts = [line[i:i+80] for i in range(0, len(line), 80)]
                yield '\n'.join(parts)
            else:
                yield line
    
    
    def write(self, text, prompt=0, color=None):
        """ write(text, prompt=0, color=None)
        
        Write to the shell. 
        
        If prompt is 0 (default) the text is printed before the prompt. If 
        prompt is 1, the text is printed after the prompt, the new prompt
        becomes null. If prompt is 2, the given text becomes the new prompt.
        
        The color of the text can also be specified (as a hex-string).
        
        """
        
        # From The Qt docs: Note that a cursor always moves when text is 
        # inserted before the current position of the cursor, and it always 
        # keeps its position when text is inserted after the current position 
        # of the cursor.
        
        # Make sure there's text and make sure its a string
        if not text:
            return
        if isinstance(text, bytes):
            text = text.decode('utf-8')
        
        # Prepare format
        format = QtGui.QTextCharFormat()
        if color:
            format.setForeground(QtGui.QColor(color))
        
        #pos1, pos2 = self._cursor1.position(), self._cursor2.position()
        
        # Just in case, clear any selection of the cursors
        self._cursor1.clearSelection()
        self._cursor2.clearSelection()
        
        if prompt == 0:
            # Insert text behind prompt (normal streams)
            self._cursor1.setKeepPositionOnInsert(False)
            self._cursor2.setKeepPositionOnInsert(False)
            text = self._handleBackspaces(text)
            if len(text) < 1024:
                # Insert text 
                self._cursor1.insertText(text, format)
            else:
                # Insert per line (very long lines are split in smaller ones)
                for line in self._splitLinesForPrinting(text):
                    self._cursor1.insertText(line, format)
        elif prompt == 1:
            # Insert command text after prompt, prompt becomes null (input)
            self._lastCommandCursor.setPosition(self._cursor2.position())
            self._cursor1.setKeepPositionOnInsert(False)
            self._cursor2.setKeepPositionOnInsert(False)
            self._cursor2.insertText(text, format)
            self._cursor1.setPosition(self._cursor2.position(), A_MOVE)
        elif prompt == 2 and text == '\b':
            # Remove prompt (used when closing the kernel)
            self._cursor1.setPosition(self._cursor2.position(), A_KEEP)
            self._cursor1.removeSelectedText()
            self._cursor2.setPosition(self._cursor1.position(), A_MOVE)
        elif prompt == 2:
            # Insert text after prompt, inserted text becomes new prompt
            self._cursor1.setPosition(self._cursor2.position(), A_MOVE)
            self._cursor1.setKeepPositionOnInsert(True)
            self._cursor2.setKeepPositionOnInsert(False)
            self._cursor1.insertText(text, format)
        
        # Reset cursor states for the user to type his/her commands
        self._cursor1.setKeepPositionOnInsert(True)
        self._cursor2.setKeepPositionOnInsert(True)
        
        # Make sure that cursor is visible (only when cursor is at edit line)
        if not self.isReadOnly():
            self.ensureCursorVisible()
        
        # Scroll along with the text if lines are popped from the top
        elif self.blockCount() == MAXBLOCKCOUNT:
            n = text.count('\n')
            sb = self.verticalScrollBar()
            sb.setValue(sb.value()-n) 
    
    
    ## Executing stuff
    
    def processLine(self, line=None, execute=True):
        """ processLine(self, line=None, execute=True)
       
        Process the given line or the current line at the prompt if not given.
        Called when the user presses enter.        
        
        If execute is False will not execute the command. This way 
        a message can be written while other ways are used to process
        the command.
        """
        
        # Can we do this?
        if self.isReadOnly() and not line:
            return
        
        if line:
            # remove trailing newline(s)
            command = line.rstrip('\n')
        else:
            # Select command
            cursor = self.textCursor()
            cursor.setPosition(self._cursor2.position(), A_MOVE)
            cursor.movePosition(cursor.End, A_KEEP)
            
            # Sample the text from the prompt and remove it
            command = cursor.selectedText().replace('\u2029', '\n') .rstrip('\n')
            cursor.removeSelectedText()
            
            # Remember the command (but first remove to prevent duplicates)
            if command:
                if command in self._history:
                    self._history.remove(command)
                self._history.insert(0,command)
        
        if execute:
            command = command.replace('\r\n', '\n')
            self.executeCommand(command+'\n')
    
    
    def executeCommand(self, command):
        """ Execute the given command. 
        Should be overridden. 
        """
        # this is a stupid simulation version
        self.write("you executed: "+command+'\n')
        self.write(">>> ", prompt=2)



class PythonShell(BaseShell):
    """ The PythonShell class implements the python part of the shell
    by connecting to a remote process that runs a Python interpreter.
    """
    
    # Emits when the status string has changed or when receiving a new prompt
    stateChanged = QtCore.Signal(BaseShell)
    
    # Emits when the debug status is changed
    debugStateChanged = QtCore.Signal(BaseShell)
    
    
    def __init__(self, parent, info):
        BaseShell.__init__(self, parent)
        
        # Get standard info if not given.
        if info is None and iep.config.shellConfigs2:
            info = iep.config.shellConfigs2[0]
        if not info:
            info = KernelInfo(None)
        
        # Store info so we can reuse it on a restart
        self._info = info
        
        # For the editor to keep track of attempted imports
        self._importAttempts = []
        
        # To keep track of the response for introspection
        self._currentCTO = None
        self._currentACO = None
        
        # Write buffer to store messages in for writing
        self._write_buffer = None
        
        # Create timer to keep polling any results
        # todo: Maybe use yoton events to process messages as they arrive.
        # I tried this briefly, but it seemd to be less efficient because 
        # messages are not so much bach-processed anymore. We should decide
        # on either method.
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_TIMER_INTERVAL)  # ms
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        
        # Add context menu
        self._menu = ShellContextMenu(shell=self, parent=self)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda p: self._menu.popup(self.mapToGlobal(p))) 
        
        # Start!
        self.resetVariables()
        self.connectToKernel(info)
    
    
    def resetVariables(self):
        """ Resets some variables. """
        
        # Reset read state
        self.setReadOnly(False)
        
        # Variables to store state, python version, builtins and keywords 
        self._state = ''
        self._debugState = {}
        self._version = ""
        self._builtins = []
        self._keywords = []
        self._startup_info = {}
        self._start_time = 0
        
        # (re)set import attempts
        self._importAttempts[:] = []
        
        # Update
        self.stateChanged.emit(self)
    
    
    def connectToKernel(self, info):
        """ connectToKernel()
        
        Create kernel and connect to it.
        
        """
        
        # Create yoton context
        self._context = ct = yoton.Context()
        
        # Create stream channels        
        self._strm_out = yoton.SubChannel(ct, 'strm-out')
        self._strm_err = yoton.SubChannel(ct, 'strm-err')
        self._strm_raw = yoton.SubChannel(ct, 'strm-raw')
        self._strm_echo = yoton.SubChannel(ct, 'strm-echo')
        self._strm_prompt = yoton.SubChannel(ct, 'strm-prompt')
        self._strm_broker = yoton.SubChannel(ct, 'strm-broker')
        self._strm_action = yoton.SubChannel(ct, 'strm-action', yoton.OBJECT)
        
        # Set channels to sync mode. This means that if the IEP cannot process
        # the messages fast enough, the sending side is blocked for a short
        # while. We don't want our users to miss any messages.
        for c in [self._strm_out, self._strm_err]:
            c.set_sync_mode(True)
        
        # Create control channels
        self._ctrl_command = yoton.PubChannel(ct, 'ctrl-command')
        self._ctrl_code = yoton.PubChannel(ct, 'ctrl-code', yoton.OBJECT)
        self._ctrl_broker = yoton.PubChannel(ct, 'ctrl-broker')
        
        # Create status channels
        self._stat_interpreter = yoton.StateChannel(ct, 'stat-interpreter')
        self._stat_debug = yoton.StateChannel(ct, 'stat-debug', yoton.OBJECT)
        self._stat_startup = yoton.StateChannel(ct, 'stat-startup', yoton.OBJECT)
        self._stat_startup.received.bind(self._onReceivedStartupInfo)
        
        # Create introspection request channel
        self._request = yoton.ReqChannel(ct, 'reqp-introspect')
        
        # Connect! The broker will only start the kernel AFTER
        # we connect, so we do not miss out on anything.
        slot = iep.localKernelManager.createKernel(finishKernelInfo(info))
        self._brokerConnection = ct.connect('localhost:%i'%slot)
        self._brokerConnection.closed.bind(self._onConnectionClose)
        
        # todo: see polling vs events
#         # Detect incoming messages 
#         for c in [self._strm_out, self._strm_err, self._strm_raw, 
#                 self._strm_echo, self._strm_prompt, self._strm_broker,
#                 self._strm_action,
#                 self._stat_interpreter, self._stat_debug]:
#             c.received.bind(self.poll)
        
    
    def _onReceivedStartupInfo(self, channel):
        startup_info = channel.recv()
        
        # Store the whole dict
        self._startup_info = startup_info
        
        # Store when we received this
        self._start_time = time.time()
        
        # Set version
        version = startup_info.get('version', None)
        if isinstance(version, tuple):
            version = [str(v) for v in version]
            self._version = '.'.join(version[:2])
        
        # Set keywords
        L = startup_info.get('keywords', None)
        if isinstance(L, list):
            self._keywords = L
        
        # Set builtins
        L = startup_info.get('builtins', None)
        if isinstance(L, list):
            self._builtins = L
        
        # Notify
        self.stateChanged.emit(self)
    
    
    ## Introspection processing methods
    
    
    def processCallTip(self, cto):
        """ Processes a calltip request using a CallTipObject instance. 
        """
        
        # Try using buffer first (not if we're not the requester)
        if self is cto.textCtrl:
            if cto.tryUsingBuffer():
                return
        
        # Clear buffer to prevent doing a second request
        # and store cto to see whether the response is still wanted.
        cto.setBuffer('')
        self._currentCTO = cto
        
        # Post request
        future = self._request.signature(cto.name)
        future.add_done_callback(self._processCallTip_response)
        future.cto = cto
    
    
    def _processCallTip_response(self, future):
        """ Process response of shell to show signature. 
        """
        
        # Process future
        if future.cancelled():
            #print('Introspect cancelled')  # No kernel
            return
        elif future.exception():
            print('Introspect-exception: ', future.exception())
            return
        else:
            response = future.result()
            cto = future.cto
        
        # First see if this is still the right editor (can also be a shell)
        editor1 = iep.editors.getCurrentEditor()
        editor2 = iep.shells.getCurrentShell()
        if cto.textCtrl not in [editor1, editor2]:
            # The editor or shell starting the autocomp is no longer active
            aco.textCtrl.autocompleteCancel()
            return
        
        # Invalid response
        if response is None:
            cto.textCtrl.autocompleteCancel()
            return
        
        # If still required, show tip, otherwise only store result
        if cto is self._currentCTO:
            cto.finish(response)
        else:
            cto.setBuffer(response)
    
    
    def processAutoComp(self, aco):
        """ Processes an autocomp request using an AutoCompObject instance. 
        """
        
        # Try using buffer first (not if we're not the requester)
        if self is aco.textCtrl:
            if aco.tryUsingBuffer():
                return
        
        # Include builtins and keywords?
        if not aco.name:
            aco.addNames(self._builtins)
            if iep.config.settings.autoComplete_keywords:
                aco.addNames(self._keywords)
        
        # Set buffer to prevent doing a second request
        # and store aco to see whether the response is still wanted.
        aco.setBuffer()
        self._currentACO = aco
        
        # Post request
        future = self._request.dir(aco.name)
        future.add_done_callback(self._processAutoComp_response)
        future.aco = aco
    
    
    def _processAutoComp_response(self, future):
        """ Process the response of the shell for the auto completion. 
        """ 
        
        # Process future
        if future.cancelled():
            #print('Introspect cancelled') # No living kernel
            return
        elif future.exception():
            print('Introspect-exception: ', future.exception())
            return
        else:
            response = future.result()
            aco = future.aco
        
        # First see if this is still the right editor (can also be a shell)
        editor1 = iep.editors.getCurrentEditor()
        editor2 = iep.shells.getCurrentShell()
        if aco.textCtrl not in [editor1, editor2]:
            # The editor or shell starting the autocomp is no longer active
            aco.textCtrl.autocompleteCancel()
            return
        
        # Add result to the list
        foundNames = []
        if response is not None:
            foundNames = response
        aco.addNames(foundNames)
        
        # Process list
        if aco.name and not foundNames:
            # No names found for the requested name. This means
            # it does not exist, let's try to import it
            importNames, importLines = iep.parser.getFictiveImports(editor1)
            baseName = aco.nameInImportNames(importNames)
            if baseName:
                line = importLines[baseName].strip()
                if line not in self._importAttempts:
                    # Do import
                    self.processLine(line + ' # auto-import')
                    self._importAttempts.append(line)
                    # Wait a barely noticable time to increase the chances
                    # That the import is complete when we repost the request.
                    time.sleep(0.2)
                    # To be sure, decrease the experiration date on the buffer
                    aco.setBuffer(timeout=1)
                    # Repost request
                    future = self._request.signature(aco.name)
                    future.add_done_callback(self._processAutoComp_response)
                    future.aco = aco
        else:
            # If still required, show list, otherwise only store result
            if self._currentACO is aco:
                aco.finish()
            else:
                aco.setBuffer()
    
    
    ## Methods for executing code
    
    
    def executeCommand(self, text):
        """ executeCommand(text)
        Execute one-line command in the remote Python session. 
        """
        self._ctrl_command.send(text)
    
    
    def executeCode(self, text, fname, lineno=0, cellName=None):
        """ executeCode(text, fname, lineno, cellName=None)
        Execute (run) a large piece of code in the remote shell.
        text: the source code to execute
        filename: the file from which the source comes
        lineno: the first lineno of the text in the file, where 0 would be
        the first line of the file...
        
        The text (source code) is first pre-processed:
        - convert all line-endings to \n
        - remove all empty lines at the end
        - remove commented lines at the end
        - convert tabs to spaces
        - dedent so minimal indentation is zero        
        """ 
        
        # Convert tabs to spaces
        text = text.replace("\t"," "*4)
        
        # Make sure there is always *some* text
        if not text:
            text = ' '
        
        # Examine the text line by line...
        # - check for empty/commented lined at the end
        # - calculate minimal indentation
        lines = text.splitlines()        
        lastLineOfCode = 0
        minIndent = 99
        for linenr in range(len(lines)):
            # Get line
            line = lines[linenr]
            # Check if empty (can be commented, but nothing more)
            tmp = line.split("#",1)[0]  # get part before first #
            if tmp.count(" ") == len(tmp):
                continue  # empty line, proceed
            else:
                lastLineOfCode = linenr
            # Calculate indentation
            tmp = line.lstrip(" ")
            indent = len(line) - len(tmp)
            if indent < minIndent:
                minIndent = indent 
        
        # Copy all proper lines to a new list, 
        # remove minimal indentation, but only if we then would only remove 
        # spaces (in the case of commented lines)
        lines2 = []
        for linenr in range(lastLineOfCode+1):
            line = lines[linenr]
            # Remove indentation, 
            if line[:minIndent].count(" ") == minIndent:
                line = line[minIndent:]
            else:
                line = line.lstrip(" ")
            lines2.append( line )
        
        
        # Send message
        text = "\n".join(lines2)
        msg = {'source':text, 'fname':fname, 'lineno':lineno, 'cellName': cellName}
        self._ctrl_code.send(msg)
    
    
    ## The polling methods and terminating methods
    
    def poll(self, channel=None):
        """ poll()
        To keep the shell up-to-date.
        Call this periodically. 
        """
        
        if self._write_buffer:
            # There is still data in the buffer
            sub, M = self._write_buffer
        else:
            # Check what subchannel has the latest message pending
            sub = yoton.select_sub_channel(self._strm_out, self._strm_err, 
                                self._strm_echo, self._strm_raw,
                                self._strm_broker, self._strm_prompt )
            # Read messages from it
            if sub:
                M = sub.recv_selected()
                #M = [sub.recv()] # Slow version (for testing)
                # Optimization: handle backspaces on stack of messages
                if sub is self._strm_out:
                    M = self._handleBackspacesOnList(M)
            # New prompt?
            if sub is self._strm_prompt:
                self.stateChanged.emit(self)
        
        # Write all pending messages that are later than any other message
        if sub:
            # Select messages to process
            N = 256
            M, buffer = M[:N], M[N:]
            # Buffer the rest
            if buffer:
                self._write_buffer = sub, buffer
            else:
                self._write_buffer = None
            # Get how to deal with prompt
            prompt = 0
            if sub is self._strm_echo:
                prompt = 1 
            elif sub is  self._strm_prompt:
                prompt = 2
            # Get color
            color = None
            if sub is self._strm_broker:
                color = '#000'
            elif sub is self._strm_raw:
                color = '#888888' # Halfway
            elif sub is self._strm_err:
                color = '#F00'
            # Write
            self.write(''.join(M), prompt, color)
        
        
        # Do any actions?
        action = self._strm_action.recv(False)
        if action:
            if action.startswith('open '):
                fname = action.split(' ',1)[1]
                iep.editors.loadFile(fname)
            else:
                print('Unkown action: %s' % action)
        
        # Update status
        state = self._stat_interpreter.recv()
        if state != self._state:
            self._state = state
            self.stateChanged.emit(self)
        
        # Update debug status
        state = self._stat_debug.recv()        
        if state != self._debugState:
            self._debugState = state
            self.debugStateChanged.emit(self)
    
    
    def interrupt(self):
        """ interrupt()
        Send a Keyboard interrupt signal to the main thread of the 
        remote process. 
        """
        self._ctrl_broker.send('INT')
    
    
    def restart(self, scriptFile=None):
        """ restart(scriptFile=None)
        Terminate the shell, after which it is restarted. 
        Args can be a filename, to execute as a script as soon as the
        shell is back up.
        """
        
        # Get info
        info = finishKernelInfo(self._info, scriptFile)
        
        # Create message and send
        msg = 'RESTART\n' + ssdf.saves(info)
        self._ctrl_broker.send(msg)
        
        # Reset
        self.resetVariables()
    
    
    def terminate(self):
        """ terminate()
        Terminates the python process. It will first try gently, but 
        if that does not work, the process shall be killed.
        To be notified of the termination, connect to the "terminated"
        signal of the shell.
        """
        self._ctrl_broker.send('TERM')
    
    
    def closeShell(self): # do not call it close(); that is a reserved method.
        """ closeShell()
        
        Very simple. This closes the shell. If possible, we will first
        tell the broker to terminate the kernel.
        
        The broker will be cleaned up if there are no clients connected
        and if there is no active kernel. In a multi-user environment,
        we should thus be able to close the shell without killing the
        kernel. But in a closed 1-to-1 environment we really want to 
        prevent loose brokers and kernels dangling around.
        
        In both cases however, it is the responsibility of the broker to
        terminate the kernel, and the shell will simply assume that this
        will work :) 
        
        """
        
        # If we can, try to tell the broker to terminate the kernel
        if self._context and self._context.connection_count:
            self.terminate()
            self._context.flush() # Important, make sure the message is send!
            self._context.close()
        
        # Adios
        iep.shells.removeShell(self)
    
    
    def _onConnectionClose(self, c, why):
        """ To be called after disconnecting.
        In general, the broker will not close the connection, so it can
        be considered an error-state if this function is called.
        """
        
        # Stop context
        if self._context:
            self._context.close()
        
        # New (empty prompt)
        self._cursor1.movePosition(self._cursor1.End, A_MOVE)
        self._cursor2.movePosition(self._cursor2.End, A_MOVE)
        
        self.write('\n\n');
        self.write('Lost connection with broker:\n')
        self.write(why)
        self.write('\n\n')
        
        # Set style to indicate dead-ness
        self.setReadOnly(True)
        
        # Goto end such that the closing message is visible
        cursor = self.textCursor()
        cursor.movePosition(cursor.End, A_MOVE)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
  
    