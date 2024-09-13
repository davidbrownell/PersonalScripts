# Local Development

## Enlistment
<!-- [BEGIN] Enlistment -->
Enlistment in this repository involves these steps.

<table>
<tr>
    <th>Step</th>
    <th>Command Line</th>
    <th>Description</th>
</tr>
<tr>
    <td>1. Clone the repository locally</td>
    <td><code>git clone https://github.com/davidbrownell/PersonalScripts</code></td>
    <td><a href="https://git-scm.com/docs/git-clone" target="_blank">https://git-scm.com/docs/git-clone</a></td>
</tr>
<tr>
    <td>2. Bootstrap the environment</td>
    <td>
        <table>
            <tr>
                <th>Linux / MacOS</th>
                <td><code>./Bootstrap.sh [--python-version &lt;python version&gt;]</code></td>
            </tr>
            <tr>
                <th>Windows</th>
                <td><code>Bootstrap.cmd [--python-version &lt;python version&gt;]</code></td>
            </tr>
        </table>
    </td>
    <td>Prepares the repository for local development by enlisting in all dependencies.</td>
</tr>
<tr>
    <td>3. Activate the environment</td>
    <td>
        <table>
            <tr>
                <th>Linux / MacOS</th>
                <td><code>. ./Activate.sh</code></td>
            </tr>
            <tr>
                <th>Windows</th>
                <td><code>Activate.cmd</code></td>
            </tr>
        </table>
    </td>
    <td>
        <p>Activates the terminal for development. Each new terminal window must be activated.</p>
        <p>Activate.sh/.cmd is actually a shortcut to the most recently bootstrapped version of python (e.g. Activate3.11.sh/.cmd). With this functionality, it is possible to support multiple python versions in the same repository and activate each in a terminal using the python-specific activation script.</p>
    </td>
</tr>
<tr>
    <td>4. [Optional] Deactivate the environment</td>
    <td>
        <table>
            <tr>
                <th>Linux / MacOS</th>
                <td><code>. ./Deactivate.sh</code></td>
            </tr>
            <tr>
                <th>Windows</th>
                <td><code>Deactivate.cmd</code></td>
            </tr>
        </table>
    </td>
    <td>
        Deactivates the terminal environment. Deactivating is optional, as the terminal window itself may be closed when development activities are complete.
    </td>
</tr>
</table>
<!-- [END] Enlistment -->

## Development Activities
No additional activities are required.
